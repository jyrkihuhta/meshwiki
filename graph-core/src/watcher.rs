//! File watching implementation using notify crate.
//!
//! Watches for changes to .md files and triggers graph updates.

use crate::events::{EventQueue, GraphEvent};
use crate::graph::WikiGraph;
use crate::parser::parse_markdown;
use notify_debouncer_mini::{
    new_debouncer,
    notify::RecursiveMode,
    DebounceEventResult, DebouncedEventKind,
};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};

/// Debounce duration for file events (500ms)
const DEBOUNCE_DURATION_MS: u64 = 500;

/// Handle for stopping the watcher thread.
pub struct WatcherHandle {
    /// Flag to signal the watcher to stop
    stop_flag: Arc<Mutex<bool>>,
    /// The watcher thread handle
    thread_handle: Option<thread::JoinHandle<()>>,
}

impl WatcherHandle {
    /// Signal the watcher to stop and wait for the thread to finish.
    pub fn stop(&mut self) {
        // Set stop flag
        if let Ok(mut flag) = self.stop_flag.lock() {
            *flag = true;
        }
        // Take the thread handle and join it
        if let Some(handle) = self.thread_handle.take() {
            let _ = handle.join();
        }
    }

    /// Check if the watcher is still running.
    pub fn is_running(&self) -> bool {
        self.thread_handle
            .as_ref()
            .map(|h| !h.is_finished())
            .unwrap_or(false)
    }
}

impl Drop for WatcherHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

/// File watcher that monitors .md files and triggers graph updates.
pub struct FileWatcher;

impl FileWatcher {
    /// Start watching a directory for changes.
    ///
    /// Spawns a background thread that:
    /// 1. Watches for file changes using notify with debouncing
    /// 2. Processes changes and generates GraphEvents
    /// 3. Pushes events to the queue for Python to poll
    ///
    /// # Arguments
    /// * `data_dir` - The directory to watch
    /// * `graph` - Arc<Mutex> wrapped graph for thread-safe updates
    /// * `event_queue` - Queue to push events for Python consumption
    ///
    /// # Returns
    /// A WatcherHandle that can be used to stop watching
    pub fn start(
        data_dir: PathBuf,
        graph: Arc<Mutex<WikiGraph>>,
        event_queue: EventQueue,
    ) -> std::io::Result<WatcherHandle> {
        let stop_flag = Arc::new(Mutex::new(false));
        let stop_flag_clone = Arc::clone(&stop_flag);
        let data_dir_clone = data_dir.clone();

        // Create channel for debounced events
        let (tx, rx) = std::sync::mpsc::channel();

        // Spawn watcher thread
        let thread_handle = thread::spawn(move || {
            // Create debouncer with 500ms timeout
            let debouncer_result = new_debouncer(
                Duration::from_millis(DEBOUNCE_DURATION_MS),
                move |res: DebounceEventResult| {
                    if let Ok(events) = res {
                        let _ = tx.send(events);
                    }
                },
            );

            let mut debouncer = match debouncer_result {
                Ok(d) => d,
                Err(e) => {
                    eprintln!("Failed to create debouncer: {:?}", e);
                    return;
                }
            };

            // Start watching the directory
            if let Err(e) = debouncer
                .watcher()
                .watch(&data_dir_clone, RecursiveMode::Recursive)
            {
                eprintln!("Failed to watch directory: {:?}", e);
                return;
            }

            // Process events until stop flag is set
            loop {
                // Check stop flag
                if let Ok(flag) = stop_flag_clone.lock() {
                    if *flag {
                        break;
                    }
                }

                // Wait for events with timeout (to check stop flag periodically)
                match rx.recv_timeout(Duration::from_millis(100)) {
                    Ok(debounced_events) => {
                        // Process the batch of events
                        let graph_events =
                            Self::process_events(&data_dir_clone, &graph, debounced_events);

                        // Push to event queue
                        if !graph_events.is_empty() {
                            event_queue.push_all(graph_events);
                        }
                    }
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                    Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                }
            }
        });

        Ok(WatcherHandle {
            stop_flag,
            thread_handle: Some(thread_handle),
        })
    }

    /// Process a batch of debounced file events.
    fn process_events(
        data_dir: &Path,
        graph: &Arc<Mutex<WikiGraph>>,
        events: Vec<notify_debouncer_mini::DebouncedEvent>,
    ) -> Vec<GraphEvent> {
        let mut graph_events = Vec::new();

        // Deduplicate paths (multiple events may fire for same file)
        let mut seen_paths: HashSet<PathBuf> = HashSet::new();

        for event in events {
            let path = &event.path;

            // Only process .md files
            if !path.extension().map_or(false, |ext| ext == "md") {
                continue;
            }

            // Skip if we've already processed this path
            if !seen_paths.insert(path.clone()) {
                continue;
            }

            // Derive page name from the path relative to data_dir so that
            // subpages are correctly identified, e.g. "Projects/MeshWiki".
            let relative_path = match path.strip_prefix(data_dir) {
                Ok(rel) => rel.to_path_buf(),
                Err(_) => continue,
            };
            let page_name = match relative_path.with_extension("").to_str() {
                Some(name) => name.replace('\\', "/"),
                None => continue,
            };

            // Handle based on event kind and file existence
            match event.kind {
                DebouncedEventKind::Any | DebouncedEventKind::AnyContinuous => {
                    if path.exists() {
                        // File exists: create or update
                        let events =
                            Self::handle_file_changed(graph, &page_name, path, &relative_path);
                        graph_events.extend(events);
                    } else {
                        // File doesn't exist: deletion
                        let events = Self::handle_file_deleted(graph, &page_name);
                        graph_events.extend(events);
                    }
                }
                // Handle any future event kinds
                _ => {}
            }
        }

        graph_events
    }

    /// Handle a file creation or modification.
    fn handle_file_changed(
        graph: &Arc<Mutex<WikiGraph>>,
        page_name: &str,
        file_path: &Path,
        relative_path: &Path,
    ) -> Vec<GraphEvent> {
        let mut events = Vec::new();

        // Read and parse the file
        let content = match fs::read_to_string(file_path) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("Failed to read {}: {}", file_path.display(), e);
                return events;
            }
        };

        let parsed = parse_markdown(&content);
        let last_modified = fs::metadata(file_path)
            .and_then(|m| m.modified())
            .unwrap_or_else(|_| SystemTime::now());

        // Lock graph for update
        let mut graph_guard = match graph.lock() {
            Ok(g) => g,
            Err(e) => {
                eprintln!("Failed to lock graph: {}", e);
                return events;
            }
        };

        // Check if page already exists (update vs create)
        let was_existing = graph_guard.page_exists(page_name);

        // Update the page in the graph
        let link_events = graph_guard.update_page(
            page_name,
            relative_path.to_path_buf(),
            parsed.metadata,
            parsed.links,
            last_modified,
        );

        // Generate appropriate event
        if was_existing {
            events.push(GraphEvent::PageUpdated {
                name: page_name.to_string(),
            });
        } else {
            events.push(GraphEvent::PageCreated {
                name: page_name.to_string(),
            });
        }

        // Add link change events
        events.extend(link_events);

        events
    }

    /// Handle a file deletion.
    fn handle_file_deleted(graph: &Arc<Mutex<WikiGraph>>, page_name: &str) -> Vec<GraphEvent> {
        let mut events = Vec::new();

        // Lock graph for update
        let mut graph_guard = match graph.lock() {
            Ok(g) => g,
            Err(e) => {
                eprintln!("Failed to lock graph: {}", e);
                return events;
            }
        };

        // Check if page exists
        if !graph_guard.page_exists(page_name) {
            return events;
        }

        // Get links before removal for events
        let outlinks = graph_guard.get_outlinks(page_name);
        let backlinks = graph_guard.get_backlinks(page_name);

        // Remove the page
        graph_guard.remove_page(page_name);

        // Generate events
        events.push(GraphEvent::PageDeleted {
            name: page_name.to_string(),
        });

        // Generate link removal events for outlinks
        for target in outlinks {
            events.push(GraphEvent::LinkRemoved {
                from: page_name.to_string(),
                to: target,
            });
        }

        // Note: We don't generate LinkRemoved events for backlinks here
        // because those links still exist in the graph (the source pages
        // still have the links, they just point to a now-deleted page).
        // The graph's remove_page handles edge cleanup automatically.
        let _ = backlinks; // Acknowledge but don't use

        events
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread::sleep;
    use tempfile::TempDir;

    #[test]
    fn test_watcher_handle_stop() {
        let graph = Arc::new(Mutex::new(WikiGraph::new()));
        let queue = EventQueue::new();

        let temp_dir = TempDir::new().unwrap();
        let mut handle = FileWatcher::start(temp_dir.path().to_path_buf(), graph, queue).unwrap();

        assert!(handle.is_running());

        handle.stop();

        // Give thread time to stop
        sleep(Duration::from_millis(200));

        assert!(!handle.is_running());
    }

    #[test]
    fn test_watcher_detects_file_creation() {
        let graph = Arc::new(Mutex::new(WikiGraph::new()));
        let queue = EventQueue::new();

        let temp_dir = TempDir::new().unwrap();
        let mut handle =
            FileWatcher::start(temp_dir.path().to_path_buf(), Arc::clone(&graph), queue.clone())
                .unwrap();

        // Create a new file
        let file_path = temp_dir.path().join("Test.md");
        fs::write(&file_path, "# Test\n\nContent").unwrap();

        // Wait for debounce + processing
        sleep(Duration::from_millis(800));

        let events = queue.drain_all();

        handle.stop();

        // Should have at least PageCreated event
        assert!(
            events
                .iter()
                .any(|e| matches!(e, GraphEvent::PageCreated { name } if name == "Test")),
            "Expected PageCreated event, got: {:?}",
            events
        );

        // Page should be in graph
        let guard = graph.lock().unwrap();
        assert!(guard.page_exists("Test"));
    }

    #[test]
    fn test_watcher_ignores_non_md_files() {
        let graph = Arc::new(Mutex::new(WikiGraph::new()));
        let queue = EventQueue::new();

        let temp_dir = TempDir::new().unwrap();
        let mut handle =
            FileWatcher::start(temp_dir.path().to_path_buf(), Arc::clone(&graph), queue.clone())
                .unwrap();

        // Create a non-.md file
        let file_path = temp_dir.path().join("notes.txt");
        fs::write(&file_path, "Some notes").unwrap();

        // Wait for debounce + processing
        sleep(Duration::from_millis(800));

        let events = queue.drain_all();

        handle.stop();

        // Should have no events for .txt file
        assert!(events.is_empty(), "Expected no events, got: {:?}", events);
    }
}
