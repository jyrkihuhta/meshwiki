//! MeshWiki Graph Engine
//!
//! A Rust-based graph engine for the MeshWiki application.
//! Provides efficient graph storage, querying, and file watching capabilities
//! through PyO3 bindings for Python integration.
//!
//! # Modules
//! - `models` - Data structures for pages and links
//! - `parser` - Markdown parsing for frontmatter and wiki links
//! - `graph` - WikiGraph implementation using petgraph
//! - `query` - Filter and MetaTable query support
//! - `events` - GraphEvent enum for file watching notifications
//! - `watcher` - File watching with notify crate

mod events;
mod graph;
mod models;
mod parser;
mod query;
mod watcher;

use pyo3::prelude::*;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

// Re-export for internal use
pub use events::{EventQueue, GraphEvent};
pub use graph::WikiGraph;
pub use models::{PageNode, ParsedLink, WikiLink};
pub use parser::{extract_wiki_links, parse_frontmatter, parse_markdown};
pub use query::{Filter, MetaTableResult, MetaTableRow, PyFilter};
pub use watcher::{FileWatcher, WatcherHandle};

/// Represents a wiki page in the graph.
///
/// This is the Python-facing page info struct that contains
/// the page name, file path, frontmatter metadata, and last modification time.
#[pyclass(from_py_object)]
#[derive(Clone, Debug)]
pub struct PageInfo {
    /// The page name (derived from filename without .md extension)
    #[pyo3(get)]
    pub name: String,

    /// The file path relative to the wiki data directory
    #[pyo3(get)]
    pub file_path: String,

    /// Frontmatter metadata as key-value pairs
    /// Values are lists to support multi-value fields (e.g., tags)
    #[pyo3(get)]
    pub metadata: HashMap<String, Vec<String>>,

    /// Last modification time as a Unix timestamp (seconds since epoch).
    /// None if the modification time could not be determined.
    #[pyo3(get)]
    pub last_modified: Option<f64>,
}

#[pymethods]
impl PageInfo {
    #[new]
    fn new(name: String, file_path: String) -> Self {
        Self {
            name,
            file_path,
            metadata: HashMap::new(),
            last_modified: None,
        }
    }

    /// Create a PageInfo with metadata.
    #[staticmethod]
    fn with_metadata(
        name: String,
        file_path: String,
        metadata: HashMap<String, Vec<String>>,
    ) -> Self {
        Self {
            name,
            file_path,
            metadata,
            last_modified: None,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "PageInfo(name='{}', file_path='{}')",
            self.name, self.file_path
        )
    }
}

impl From<&PageNode> for PageInfo {
    fn from(node: &PageNode) -> Self {
        let last_modified = node
            .last_modified
            .duration_since(std::time::UNIX_EPOCH)
            .ok()
            .map(|d| d.as_secs_f64());
        PageInfo {
            name: node.name.clone(),
            file_path: node.file_path.to_string_lossy().to_string(),
            metadata: node.metadata.clone(),
            last_modified,
        }
    }
}

/// The main graph engine that manages wiki pages and their relationships.
///
/// Provides methods for:
/// - Listing and retrieving pages
/// - Finding backlinks and outlinks
/// - Querying pages by metadata
/// - Watching for file changes
///
/// # Example
/// ```python
/// from graph_core import GraphEngine
///
/// engine = GraphEngine("/path/to/wiki/data")
/// engine.rebuild()  # Scan directory and build graph
///
/// pages = engine.list_pages()
/// for page in pages:
///     print(f"{page.name}: {page.metadata}")
///
/// # Start watching for changes
/// engine.start_watching()
///
/// # Later, poll for events
/// events = engine.poll_events()
/// for event in events:
///     print(f"{event.event_type()}: {event.page_name()}")
///
/// engine.stop_watching()
/// ```
#[pyclass]
pub struct GraphEngine {
    /// The root directory containing wiki markdown files
    data_dir: PathBuf,
    /// The wiki graph (wrapped for thread-safe access when watching)
    graph: Arc<Mutex<WikiGraph>>,
    /// Event queue for file watcher notifications
    event_queue: EventQueue,
    /// Handle to the file watcher (if watching)
    watcher_handle: Option<WatcherHandle>,
}

#[pymethods]
impl GraphEngine {
    /// Create a new GraphEngine instance.
    ///
    /// # Arguments
    /// * `data_dir` - Path to the directory containing wiki markdown files
    ///
    /// # Example
    /// ```python
    /// from graph_core import GraphEngine
    /// engine = GraphEngine("/path/to/wiki/data")
    /// ```
    #[new]
    fn new(data_dir: &str) -> PyResult<Self> {
        Ok(Self {
            data_dir: PathBuf::from(data_dir),
            graph: Arc::new(Mutex::new(WikiGraph::new())),
            event_queue: EventQueue::new(),
            watcher_handle: None,
        })
    }

    /// Get the data directory path.
    ///
    /// Returns the path to the wiki data directory as a string.
    fn get_data_dir(&self) -> String {
        self.data_dir.to_string_lossy().to_string()
    }

    /// List all pages in the graph.
    ///
    /// Returns a list of PageInfo objects for all pages currently
    /// tracked by the graph engine.
    ///
    /// # Example
    /// ```python
    /// engine = GraphEngine("/path/to/data")
    /// engine.rebuild()
    /// pages = engine.list_pages()
    /// for page in pages:
    ///     print(page.name)
    /// ```
    fn list_pages(&self) -> PyResult<Vec<PageInfo>> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.list_pages().iter().map(|p| PageInfo::from(*p)).collect())
    }

    /// Get a specific page by name.
    ///
    /// # Arguments
    /// * `name` - The page name to look up
    ///
    /// # Returns
    /// The PageInfo if found, None otherwise
    fn get_page(&self, name: &str) -> PyResult<Option<PageInfo>> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.get_page(name).map(PageInfo::from))
    }

    /// Check if a page exists in the graph.
    ///
    /// # Arguments
    /// * `name` - The page name to check
    ///
    /// # Returns
    /// True if the page exists, False otherwise
    fn page_exists(&self, name: &str) -> PyResult<bool> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.page_exists(name))
    }

    /// Get the number of pages in the graph.
    fn page_count(&self) -> PyResult<usize> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.page_count())
    }

    /// Get the number of links in the graph.
    fn link_count(&self) -> PyResult<usize> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.link_count())
    }

    /// Get backlinks for a page (pages that link to this page).
    ///
    /// # Arguments
    /// * `name` - The page name to find backlinks for
    ///
    /// # Returns
    /// A list of page names that link to the specified page.
    ///
    /// # Example
    /// ```python
    /// backlinks = engine.get_backlinks("HomePage")
    /// print(f"Pages linking to HomePage: {backlinks}")
    /// ```
    fn get_backlinks(&self, name: &str) -> PyResult<Vec<String>> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.get_backlinks(name))
    }

    /// Get outlinks for a page (pages that this page links to).
    ///
    /// # Arguments
    /// * `name` - The page name to find outlinks for
    ///
    /// # Returns
    /// A list of page names that the specified page links to.
    ///
    /// # Example
    /// ```python
    /// outlinks = engine.get_outlinks("HomePage")
    /// print(f"HomePage links to: {outlinks}")
    /// ```
    fn get_outlinks(&self, name: &str) -> PyResult<Vec<String>> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.get_outlinks(name))
    }

    /// Rebuild the graph by scanning all markdown files.
    ///
    /// This will clear the current graph and rescan all files
    /// in the data directory, parsing frontmatter and extracting
    /// wiki links to build the graph.
    ///
    /// Note: If file watching is active, it will be temporarily stopped
    /// during rebuild and restarted after.
    ///
    /// # Returns
    /// Result indicating success. Raises an exception on IO errors.
    ///
    /// # Example
    /// ```python
    /// engine = GraphEngine("/path/to/wiki")
    /// engine.rebuild()  # Scan and build graph
    /// print(f"Found {engine.page_count()} pages")
    /// ```
    fn rebuild(&mut self) -> PyResult<()> {
        // Stop watcher during rebuild
        let was_watching = self.is_watching();
        if was_watching {
            self.stop_watching()?;
        }

        // Rebuild graph
        {
            let mut graph = self.graph.lock().map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Failed to acquire graph lock: {}",
                    e
                ))
            })?;
            graph
                .build_from_directory(&self.data_dir)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
        }

        // Restart watcher if it was running
        if was_watching {
            self.start_watching()?;
        }

        Ok(())
    }

    /// Get metadata for a specific page.
    ///
    /// # Arguments
    /// * `name` - The page name to get metadata for
    ///
    /// # Returns
    /// The metadata dictionary if the page exists, None otherwise.
    fn get_metadata(&self, name: &str) -> PyResult<Option<HashMap<String, Vec<String>>>> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(graph.get_page(name).map(|p| p.metadata.clone()))
    }

    /// Query pages that match all filters.
    ///
    /// Filters are combined with AND logic - a page must match all filters
    /// to be included in the results.
    ///
    /// # Arguments
    /// * `filters` - List of Filter objects to apply
    ///
    /// # Returns
    /// List of PageInfo objects matching all filters.
    ///
    /// # Example
    /// ```python
    /// from graph_core import GraphEngine, Filter
    ///
    /// engine = GraphEngine("/path/to/wiki")
    /// engine.rebuild()
    ///
    /// # Find all draft pages
    /// drafts = engine.query([Filter.equals("status", "draft")])
    ///
    /// # Find pages with tag "rust" that link to HomePage
    /// results = engine.query([
    ///     Filter.equals("tags", "rust"),
    ///     Filter.links_to("HomePage")
    /// ])
    /// ```
    fn query(&self, filters: Vec<PyFilter>) -> PyResult<Vec<PageInfo>> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        let rust_filters: Vec<Filter> = filters.iter().map(|f| f.inner.clone()).collect();
        Ok(graph
            .query(&rust_filters)
            .iter()
            .map(|p| PageInfo::from(*p))
            .collect())
    }

    /// MetaTable query: filter pages and select specific columns.
    ///
    /// Returns a structured result containing only the requested columns
    /// for pages matching all filters.
    ///
    /// # Arguments
    /// * `filters` - List of Filter objects to apply
    /// * `columns` - List of column names to include in results
    ///
    /// # Returns
    /// MetaTableResult with rows containing the selected columns.
    ///
    /// # Example
    /// ```python
    /// from graph_core import GraphEngine, Filter
    ///
    /// engine = GraphEngine("/path/to/wiki")
    /// engine.rebuild()
    ///
    /// # Get status and author for all draft pages
    /// result = engine.metatable(
    ///     [Filter.equals("status", "draft")],
    ///     ["name", "status", "author"]
    /// )
    /// for row in result:
    ///     print(f"{row.page_name}: {row.get('author')}")
    /// ```
    fn metatable(&self, filters: Vec<PyFilter>, columns: Vec<String>) -> PyResult<MetaTableResult> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        let rust_filters: Vec<Filter> = filters.iter().map(|f| f.inner.clone()).collect();
        Ok(graph.metatable(&rust_filters, &columns))
    }

    // ========== File Watching API ==========

    /// Start watching the data directory for changes.
    ///
    /// This spawns a background thread that monitors for file changes
    /// and updates the graph accordingly. Use poll_events() to retrieve
    /// the generated GraphEvents.
    ///
    /// # Example
    /// ```python
    /// engine = GraphEngine("/path/to/wiki")
    /// engine.rebuild()
    /// engine.start_watching()
    ///
    /// # Later, poll for events
    /// events = engine.poll_events()
    /// for event in events:
    ///     print(f"{event.event_type()}: {event.page_name()}")
    ///
    /// # When done
    /// engine.stop_watching()
    /// ```
    fn start_watching(&mut self) -> PyResult<()> {
        // Stop existing watcher if any
        if let Some(mut handle) = self.watcher_handle.take() {
            handle.stop();
        }

        // Start new watcher
        let handle = FileWatcher::start(
            self.data_dir.clone(),
            Arc::clone(&self.graph),
            self.event_queue.clone(),
        )
        .map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to start file watcher: {}", e))
        })?;

        self.watcher_handle = Some(handle);
        Ok(())
    }

    /// Stop watching for file changes.
    ///
    /// This stops the background watcher thread. Any unpolled events
    /// will remain in the queue.
    fn stop_watching(&mut self) -> PyResult<()> {
        if let Some(mut handle) = self.watcher_handle.take() {
            handle.stop();
        }
        Ok(())
    }

    /// Check if the file watcher is currently running.
    fn is_watching(&self) -> bool {
        self.watcher_handle
            .as_ref()
            .map(|h| h.is_running())
            .unwrap_or(false)
    }

    /// Poll for graph events.
    ///
    /// Returns all events that have been queued since the last poll.
    /// Events are removed from the queue after polling.
    ///
    /// # Returns
    /// A list of GraphEvent objects representing changes to the graph.
    ///
    /// # Example
    /// ```python
    /// events = engine.poll_events()
    /// for event in events:
    ///     if event.event_type() == "page_created":
    ///         print(f"New page: {event.page_name()}")
    ///     elif event.event_type() == "link_created":
    ///         print(f"New link: {event.link_from()} -> {event.link_to()}")
    /// ```
    fn poll_events(&self) -> Vec<GraphEvent> {
        self.event_queue.drain_all()
    }

    /// Check if there are pending events.
    fn has_pending_events(&self) -> bool {
        !self.event_queue.is_empty()
    }

    fn __repr__(&self) -> PyResult<String> {
        let graph = self.graph.lock().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to acquire graph lock: {}", e))
        })?;
        Ok(format!(
            "GraphEngine(data_dir='{}', pages={}, links={}, watching={})",
            self.data_dir.display(),
            graph.page_count(),
            graph.link_count(),
            self.is_watching()
        ))
    }
}

/// Python module initialization.
///
/// This function is called when the module is imported in Python.
/// It registers all exported classes and functions.
#[pymodule]
fn graph_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<GraphEngine>()?;
    m.add_class::<PageInfo>()?;
    m.add_class::<PyFilter>()?;
    m.add_class::<MetaTableResult>()?;
    m.add_class::<MetaTableRow>()?;
    m.add_class::<GraphEvent>()?;

    // Add module-level version info
    m.add("__version__", "0.1.0")?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_page_info_creation() {
        let page = PageInfo::new("TestPage".to_string(), "TestPage.md".to_string());
        assert_eq!(page.name, "TestPage");
        assert_eq!(page.file_path, "TestPage.md");
        assert!(page.metadata.is_empty());
    }

    #[test]
    fn test_page_info_with_metadata() {
        let mut metadata = HashMap::new();
        metadata.insert("status".to_string(), vec!["draft".to_string()]);

        let page = PageInfo::with_metadata(
            "TestPage".to_string(),
            "TestPage.md".to_string(),
            metadata,
        );

        assert_eq!(page.name, "TestPage");
        assert_eq!(
            page.metadata.get("status"),
            Some(&vec!["draft".to_string()])
        );
    }

    #[test]
    fn test_page_info_from_page_node() {
        let mut metadata = HashMap::new();
        metadata.insert(
            "tags".to_string(),
            vec!["rust".to_string(), "wiki".to_string()],
        );

        let node = PageNode::with_metadata(
            "TestNode".to_string(),
            PathBuf::from("test/TestNode.md"),
            metadata.clone(),
            std::time::SystemTime::now(),
        );

        let info = PageInfo::from(&node);
        assert_eq!(info.name, "TestNode");
        assert_eq!(info.file_path, "test/TestNode.md");
        assert_eq!(info.metadata, metadata);
    }

    #[test]
    fn test_graph_engine_creation() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        assert_eq!(engine.get_data_dir(), "/tmp/test");
        assert_eq!(engine.page_count().unwrap(), 0);
        assert_eq!(engine.link_count().unwrap(), 0);
        assert!(!engine.is_watching());
    }

    #[test]
    fn test_list_pages_empty() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        let pages = engine.list_pages().unwrap();
        assert!(pages.is_empty());
    }

    #[test]
    fn test_page_exists_false() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        assert!(!engine.page_exists("NonExistent").unwrap());
    }

    #[test]
    fn test_get_page_none() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        assert!(engine.get_page("NonExistent").unwrap().is_none());
    }

    #[test]
    fn test_backlinks_empty() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        let backlinks = engine.get_backlinks("SomePage").unwrap();
        assert!(backlinks.is_empty());
    }

    #[test]
    fn test_outlinks_empty() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        let outlinks = engine.get_outlinks("SomePage").unwrap();
        assert!(outlinks.is_empty());
    }

    #[test]
    fn test_poll_events_empty() {
        let engine = GraphEngine::new("/tmp/test").unwrap();
        let events = engine.poll_events();
        assert!(events.is_empty());
        assert!(!engine.has_pending_events());
    }
}
