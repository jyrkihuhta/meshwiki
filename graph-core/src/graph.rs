//! Wiki graph implementation using petgraph.
//!
//! This module provides the core graph structure for storing wiki pages
//! and their link relationships. It uses petgraph for efficient graph
//! operations and provides methods for querying backlinks and outlinks.

use crate::events::GraphEvent;
use crate::models::{PageNode, ParsedLink, WikiLink};
use crate::parser::parse_markdown;
use crate::query::{matches_all_filters, Filter, MetaTableResult, MetaTableRow};
use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::visit::EdgeRef;
use petgraph::Direction;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

/// The wiki graph structure.
///
/// Wraps a directed graph from petgraph with additional lookup structures
/// for fast page name to node index resolution.
pub struct WikiGraph {
    /// The underlying directed graph.
    /// Nodes are PageNode, edges are WikiLink.
    graph: DiGraph<PageNode, WikiLink>,

    /// Map from page name to node index for O(1) lookups.
    node_index: HashMap<String, NodeIndex>,
}

impl WikiGraph {
    /// Create a new empty WikiGraph.
    pub fn new() -> Self {
        Self {
            graph: DiGraph::new(),
            node_index: HashMap::new(),
        }
    }

    /// Add a page to the graph.
    ///
    /// If a page with the same name already exists, it will be updated.
    ///
    /// # Arguments
    /// * `page` - The PageNode to add
    ///
    /// # Returns
    /// The NodeIndex of the added or updated page.
    pub fn add_page(&mut self, page: PageNode) -> NodeIndex {
        if let Some(&idx) = self.node_index.get(&page.name) {
            // Update existing node
            self.graph[idx] = page;
            idx
        } else {
            // Add new node
            let name = page.name.clone();
            let idx = self.graph.add_node(page);
            self.node_index.insert(name, idx);
            idx
        }
    }

    /// Get a page by name.
    ///
    /// # Arguments
    /// * `name` - The page name to look up
    ///
    /// # Returns
    /// A reference to the PageNode if found, None otherwise.
    pub fn get_page(&self, name: &str) -> Option<&PageNode> {
        self.node_index
            .get(name)
            .map(|&idx| &self.graph[idx])
    }

    /// Check if a page exists in the graph.
    pub fn page_exists(&self, name: &str) -> bool {
        self.node_index.contains_key(name)
    }

    /// Get the number of pages in the graph.
    pub fn page_count(&self) -> usize {
        self.graph.node_count()
    }

    /// Get the number of links in the graph.
    pub fn link_count(&self) -> usize {
        self.graph.edge_count()
    }

    /// List all pages in the graph.
    ///
    /// # Returns
    /// A vector of references to all PageNodes.
    pub fn list_pages(&self) -> Vec<&PageNode> {
        self.graph.node_weights().collect()
    }

    /// Add a link between two pages.
    ///
    /// # Arguments
    /// * `from` - The source page name
    /// * `to` - The target page name
    /// * `link` - The WikiLink edge data
    ///
    /// # Returns
    /// true if the link was added, false if either page doesn't exist.
    pub fn add_link(&mut self, from: &str, to: &str, link: WikiLink) -> bool {
        let from_idx = self.node_index.get(from).copied();
        let to_idx = self.node_index.get(to).copied();

        match (from_idx, to_idx) {
            (Some(from), Some(to)) => {
                // Check if link already exists
                if !self.graph.contains_edge(from, to) {
                    self.graph.add_edge(from, to, link);
                }
                true
            }
            _ => false,
        }
    }

    /// Get backlinks for a page (pages that link TO this page).
    ///
    /// # Arguments
    /// * `name` - The page name to find backlinks for
    ///
    /// # Returns
    /// A vector of page names that link to the specified page.
    pub fn get_backlinks(&self, name: &str) -> Vec<String> {
        let target_idx = match self.node_index.get(name) {
            Some(&idx) => idx,
            None => return Vec::new(),
        };

        self.graph
            .neighbors_directed(target_idx, Direction::Incoming)
            .map(|idx| self.graph[idx].name.clone())
            .collect()
    }

    /// Get outlinks for a page (pages that this page links TO).
    ///
    /// # Arguments
    /// * `name` - The page name to find outlinks for
    ///
    /// # Returns
    /// A vector of page names that the specified page links to.
    pub fn get_outlinks(&self, name: &str) -> Vec<String> {
        let source_idx = match self.node_index.get(name) {
            Some(&idx) => idx,
            None => return Vec::new(),
        };

        self.graph
            .neighbors_directed(source_idx, Direction::Outgoing)
            .map(|idx| self.graph[idx].name.clone())
            .collect()
    }

    /// Clear the graph, removing all pages and links.
    pub fn clear(&mut self) {
        self.graph.clear();
        self.node_index.clear();
    }

    /// Remove a page and all its edges from the graph.
    ///
    /// Note: petgraph uses swap-remove semantics, so when a node is removed,
    /// the last node in the graph takes its index. This method handles
    /// updating the node_index HashMap accordingly.
    ///
    /// # Arguments
    /// * `name` - The page name to remove
    ///
    /// # Returns
    /// true if the page was removed, false if it didn't exist.
    pub fn remove_page(&mut self, name: &str) -> bool {
        if let Some(idx) = self.node_index.remove(name) {
            // Get the index of the last node before removal
            let last_idx = NodeIndex::new(self.graph.node_count() - 1);

            // If we're not removing the last node, we need to update the index
            // of the node that will be swapped into this position
            if idx != last_idx {
                // Get the name of the node that will be swapped
                if let Some(swapped_node) = self.graph.node_weight(last_idx) {
                    let swapped_name = swapped_node.name.clone();
                    // After removal, the swapped node will have index `idx`
                    self.node_index.insert(swapped_name, idx);
                }
            }

            // Remove the node (petgraph handles edge cleanup)
            self.graph.remove_node(idx);
            true
        } else {
            false
        }
    }

    /// Remove all outgoing edges from a page.
    ///
    /// Used when updating a page's links to clear old links before adding new ones.
    ///
    /// # Arguments
    /// * `name` - The page name to remove outgoing edges from
    pub fn remove_outgoing_edges(&mut self, name: &str) {
        if let Some(&idx) = self.node_index.get(name) {
            // Collect edges to remove (can't modify while iterating)
            let edges_to_remove: Vec<_> = self
                .graph
                .edges_directed(idx, Direction::Outgoing)
                .map(|e| e.id())
                .collect();

            for edge_id in edges_to_remove {
                self.graph.remove_edge(edge_id);
            }
        }
    }

    /// Update a page with new content, returning link change events.
    ///
    /// This method:
    /// 1. Updates or creates the page node
    /// 2. Removes old outgoing links
    /// 3. Adds new outgoing links
    /// 4. Returns events for link changes
    ///
    /// # Arguments
    /// * `name` - The page name
    /// * `file_path` - Relative path to the file
    /// * `metadata` - Parsed frontmatter metadata
    /// * `links` - Parsed wiki links
    /// * `last_modified` - File modification time
    ///
    /// # Returns
    /// A vector of GraphEvents for link changes (LinkCreated, LinkRemoved)
    pub fn update_page(
        &mut self,
        name: &str,
        file_path: PathBuf,
        metadata: HashMap<String, Vec<String>>,
        links: Vec<ParsedLink>,
        last_modified: SystemTime,
    ) -> Vec<GraphEvent> {
        let mut events = Vec::new();

        // Get old outlinks before update
        let old_outlinks: HashSet<String> = self.get_outlinks(name).into_iter().collect();

        // Create/update the page node
        let page = PageNode::with_metadata(name.to_string(), file_path, metadata, last_modified);
        self.add_page(page);

        // Remove all existing outgoing edges
        self.remove_outgoing_edges(name);

        // Add new links
        let mut new_outlinks: HashSet<String> = HashSet::new();

        for link in &links {
            // Ensure target page exists (create stub if needed)
            if !self.page_exists(&link.target) {
                let stub = PageNode::new(
                    link.target.clone(),
                    PathBuf::from(format!("{}.md", link.target)),
                );
                self.add_page(stub);
            }

            // Add the link
            let wiki_link = match &link.display_text {
                Some(text) => WikiLink::with_display_text(text.clone()),
                None => WikiLink::new(),
            };
            self.add_link(name, &link.target, wiki_link);
            new_outlinks.insert(link.target.clone());
        }

        // Generate link change events
        // Links removed: in old but not in new
        for target in old_outlinks.difference(&new_outlinks) {
            events.push(GraphEvent::LinkRemoved {
                from: name.to_string(),
                to: target.clone(),
            });
        }

        // Links created: in new but not in old
        for target in new_outlinks.difference(&old_outlinks) {
            events.push(GraphEvent::LinkCreated {
                from: name.to_string(),
                to: target.clone(),
            });
        }

        events
    }

    /// Query pages that match all filters.
    ///
    /// Returns references to all PageNodes that match every filter
    /// in the provided list (AND logic).
    ///
    /// # Arguments
    /// * `filters` - Slice of filters to apply. All must match (AND logic).
    ///
    /// # Returns
    /// A vector of references to matching PageNodes.
    pub fn query(&self, filters: &[Filter]) -> Vec<&PageNode> {
        self.graph
            .node_weights()
            .filter(|page| matches_all_filters(page, filters, self))
            .collect()
    }

    /// MetaTable query: filter pages and select specific columns.
    ///
    /// Returns a structured result with rows containing only the
    /// requested metadata columns.
    ///
    /// # Arguments
    /// * `filters` - Slice of filters to apply
    /// * `columns` - Column names to include in results
    ///
    /// # Returns
    /// A MetaTableResult containing the matching rows with selected columns.
    ///
    /// # Special columns
    /// - `name` - The page name (always available)
    /// - `file_path` - The file path (always available)
    /// - Any metadata key from frontmatter
    pub fn metatable(&self, filters: &[Filter], columns: &[String]) -> MetaTableResult {
        let matching_pages = self.query(filters);

        let rows: Vec<MetaTableRow> = matching_pages
            .iter()
            .map(|page| {
                let values: HashMap<String, Vec<String>> = columns
                    .iter()
                    .filter_map(|col| {
                        if col == "name" {
                            Some((col.clone(), vec![page.name.clone()]))
                        } else if col == "file_path" {
                            Some((
                                col.clone(),
                                vec![page.file_path.to_string_lossy().to_string()],
                            ))
                        } else {
                            page.metadata.get(col).map(|v| (col.clone(), v.clone()))
                        }
                    })
                    .collect();

                MetaTableRow {
                    page_name: page.name.clone(),
                    values,
                }
            })
            .collect();

        MetaTableResult {
            columns: columns.to_vec(),
            rows,
        }
    }

    /// Build the graph from a directory of markdown files.
    ///
    /// Scans the directory for .md files, parses each one to extract
    /// metadata and links, and builds the graph.
    ///
    /// # Arguments
    /// * `dir` - The directory path to scan
    ///
    /// # Returns
    /// Result indicating success or an IO error.
    pub fn build_from_directory(&mut self, dir: &Path) -> io::Result<()> {
        self.clear();

        // Collect all markdown files and their parsed data
        let mut parsed_pages: Vec<(String, PathBuf, ParsedPageData)> = Vec::new();

        self.scan_directory(dir, dir, &mut parsed_pages)?;

        // First pass: add all pages as nodes
        for (name, file_path, data) in &parsed_pages {
            let node = PageNode::with_metadata(
                name.clone(),
                file_path.clone(),
                data.metadata.clone(),
                data.last_modified,
            );
            self.add_page(node);
        }

        // Second pass: add links
        // We need to handle links to pages that might not exist (create stub nodes)
        for (name, _, data) in &parsed_pages {
            for link in &data.links {
                // Ensure target page exists (create stub if needed)
                if !self.page_exists(&link.target) {
                    // Create a stub node for the missing page
                    let stub = PageNode::new(
                        link.target.clone(),
                        PathBuf::from(format!("{}.md", link.target)),
                    );
                    self.add_page(stub);
                }

                // Add the link
                let wiki_link = match &link.display_text {
                    Some(text) => WikiLink::with_display_text(text.clone()),
                    None => WikiLink::new(),
                };
                self.add_link(name, &link.target, wiki_link);
            }
        }

        Ok(())
    }

    /// Recursively scan a directory for markdown files.
    fn scan_directory(
        &self,
        base_dir: &Path,
        current_dir: &Path,
        results: &mut Vec<(String, PathBuf, ParsedPageData)>,
    ) -> io::Result<()> {
        if !current_dir.is_dir() {
            return Ok(());
        }

        for entry in fs::read_dir(current_dir)? {
            let entry = entry?;
            let path = entry.path();

            if path.is_dir() {
                self.scan_directory(base_dir, &path, results)?;
            } else if path.extension().map_or(false, |ext| ext == "md") {
                // Get the relative path from base_dir
                let relative_path = path.strip_prefix(base_dir).unwrap_or(&path).to_path_buf();

                // Derive page name from relative path (without .md extension).
                // Using the full relative path preserves subpage structure,
                // e.g. "Projects/MeshWiki.md" → "Projects/MeshWiki".
                let name = relative_path
                    .with_extension("")
                    .to_str()
                    .unwrap_or("unknown")
                    .replace('\\', "/");

                // Get file modification time
                let last_modified = entry
                    .metadata()
                    .and_then(|m| m.modified())
                    .unwrap_or_else(|_| SystemTime::now());

                // Read and parse the file
                match fs::read_to_string(&path) {
                    Ok(content) => {
                        let parsed = parse_markdown(&content);
                        results.push((
                            name,
                            relative_path,
                            ParsedPageData {
                                metadata: parsed.metadata,
                                links: parsed.links,
                                last_modified,
                            },
                        ));
                    }
                    Err(e) => {
                        eprintln!("Warning: Failed to read {}: {}", path.display(), e);
                    }
                }
            }
        }

        Ok(())
    }
}

impl Default for WikiGraph {
    fn default() -> Self {
        Self::new()
    }
}

/// Internal struct to hold parsed page data during directory scan.
struct ParsedPageData {
    metadata: HashMap<String, Vec<String>>,
    links: Vec<ParsedLink>,
    last_modified: SystemTime,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_graph() {
        let graph = WikiGraph::new();
        assert_eq!(graph.page_count(), 0);
        assert_eq!(graph.link_count(), 0);
    }

    #[test]
    fn test_add_page() {
        let mut graph = WikiGraph::new();
        let page = PageNode::new("TestPage".to_string(), PathBuf::from("TestPage.md"));
        let _idx = graph.add_page(page);

        assert_eq!(graph.page_count(), 1);
        assert!(graph.page_exists("TestPage"));
        assert!(graph.get_page("TestPage").is_some());
    }

    #[test]
    fn test_add_page_update() {
        let mut graph = WikiGraph::new();

        let page1 = PageNode::new("TestPage".to_string(), PathBuf::from("old.md"));
        graph.add_page(page1);

        let page2 = PageNode::new("TestPage".to_string(), PathBuf::from("new.md"));
        graph.add_page(page2);

        assert_eq!(graph.page_count(), 1);
        assert_eq!(
            graph.get_page("TestPage").unwrap().file_path,
            PathBuf::from("new.md")
        );
    }

    #[test]
    fn test_list_pages() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Page1".to_string(), PathBuf::from("1.md")));
        graph.add_page(PageNode::new("Page2".to_string(), PathBuf::from("2.md")));

        let pages = graph.list_pages();
        assert_eq!(pages.len(), 2);

        let names: Vec<&str> = pages.iter().map(|p| p.name.as_str()).collect();
        assert!(names.contains(&"Page1"));
        assert!(names.contains(&"Page2"));
    }

    #[test]
    fn test_add_link() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Page1".to_string(), PathBuf::from("1.md")));
        graph.add_page(PageNode::new("Page2".to_string(), PathBuf::from("2.md")));

        assert!(graph.add_link("Page1", "Page2", WikiLink::new()));
        assert_eq!(graph.link_count(), 1);
    }

    #[test]
    fn test_add_link_missing_page() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Page1".to_string(), PathBuf::from("1.md")));

        // Link to non-existent page should fail
        assert!(!graph.add_link("Page1", "NonExistent", WikiLink::new()));
        assert_eq!(graph.link_count(), 0);
    }

    #[test]
    fn test_get_backlinks() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("HomePage".to_string(), PathBuf::from("home.md")));
        graph.add_page(PageNode::new("About".to_string(), PathBuf::from("about.md")));
        graph.add_page(PageNode::new("Contact".to_string(), PathBuf::from("contact.md")));

        // About and Contact both link to HomePage
        graph.add_link("About", "HomePage", WikiLink::new());
        graph.add_link("Contact", "HomePage", WikiLink::new());

        let backlinks = graph.get_backlinks("HomePage");
        assert_eq!(backlinks.len(), 2);
        assert!(backlinks.contains(&"About".to_string()));
        assert!(backlinks.contains(&"Contact".to_string()));
    }

    #[test]
    fn test_get_backlinks_none() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Orphan".to_string(), PathBuf::from("orphan.md")));

        let backlinks = graph.get_backlinks("Orphan");
        assert!(backlinks.is_empty());
    }

    #[test]
    fn test_get_backlinks_nonexistent() {
        let graph = WikiGraph::new();
        let backlinks = graph.get_backlinks("NonExistent");
        assert!(backlinks.is_empty());
    }

    #[test]
    fn test_get_outlinks() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Index".to_string(), PathBuf::from("index.md")));
        graph.add_page(PageNode::new("About".to_string(), PathBuf::from("about.md")));
        graph.add_page(PageNode::new("Contact".to_string(), PathBuf::from("contact.md")));

        // Index links to both About and Contact
        graph.add_link("Index", "About", WikiLink::new());
        graph.add_link("Index", "Contact", WikiLink::new());

        let outlinks = graph.get_outlinks("Index");
        assert_eq!(outlinks.len(), 2);
        assert!(outlinks.contains(&"About".to_string()));
        assert!(outlinks.contains(&"Contact".to_string()));
    }

    #[test]
    fn test_clear() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Page1".to_string(), PathBuf::from("1.md")));
        graph.add_page(PageNode::new("Page2".to_string(), PathBuf::from("2.md")));
        graph.add_link("Page1", "Page2", WikiLink::new());

        assert_eq!(graph.page_count(), 2);
        assert_eq!(graph.link_count(), 1);

        graph.clear();

        assert_eq!(graph.page_count(), 0);
        assert_eq!(graph.link_count(), 0);
        assert!(!graph.page_exists("Page1"));
    }

    // Note: build_from_directory tests require tempfile crate
    // which is added as a dev-dependency

    #[test]
    fn test_remove_page() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Page1".to_string(), PathBuf::from("1.md")));
        graph.add_page(PageNode::new("Page2".to_string(), PathBuf::from("2.md")));
        graph.add_link("Page1", "Page2", WikiLink::new());

        assert!(graph.remove_page("Page1"));
        assert!(!graph.page_exists("Page1"));
        assert_eq!(graph.page_count(), 1);
        assert_eq!(graph.link_count(), 0); // Link should be removed too
    }

    #[test]
    fn test_remove_page_nonexistent() {
        let mut graph = WikiGraph::new();
        assert!(!graph.remove_page("NonExistent"));
    }

    #[test]
    fn test_remove_page_updates_index() {
        // Test that node_index is correctly updated after swap-remove
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("First".to_string(), PathBuf::from("1.md")));
        graph.add_page(PageNode::new("Second".to_string(), PathBuf::from("2.md")));
        graph.add_page(PageNode::new("Third".to_string(), PathBuf::from("3.md")));

        // Remove first page - Third should be swapped into its position
        graph.remove_page("First");

        assert!(!graph.page_exists("First"));
        assert!(graph.page_exists("Second"));
        assert!(graph.page_exists("Third"));
        assert_eq!(graph.page_count(), 2);

        // Verify we can still get the pages correctly
        assert!(graph.get_page("Second").is_some());
        assert!(graph.get_page("Third").is_some());
    }

    #[test]
    fn test_remove_outgoing_edges() {
        let mut graph = WikiGraph::new();
        graph.add_page(PageNode::new("Source".to_string(), PathBuf::from("s.md")));
        graph.add_page(PageNode::new("Target1".to_string(), PathBuf::from("t1.md")));
        graph.add_page(PageNode::new("Target2".to_string(), PathBuf::from("t2.md")));

        graph.add_link("Source", "Target1", WikiLink::new());
        graph.add_link("Source", "Target2", WikiLink::new());

        assert_eq!(graph.link_count(), 2);
        assert_eq!(graph.get_outlinks("Source").len(), 2);

        graph.remove_outgoing_edges("Source");

        assert_eq!(graph.link_count(), 0);
        assert_eq!(graph.get_outlinks("Source").len(), 0);
    }

    #[test]
    fn test_update_page_new() {
        let mut graph = WikiGraph::new();

        let events = graph.update_page(
            "NewPage",
            PathBuf::from("NewPage.md"),
            HashMap::new(),
            vec![ParsedLink::new("Target".to_string(), None)],
            SystemTime::now(),
        );

        assert!(graph.page_exists("NewPage"));
        assert!(graph.page_exists("Target")); // Stub created
        assert_eq!(graph.link_count(), 1);

        // Should have one LinkCreated event
        assert_eq!(events.len(), 1);
        assert!(matches!(
            &events[0],
            GraphEvent::LinkCreated { from, to } if from == "NewPage" && to == "Target"
        ));
    }

    #[test]
    fn test_update_page_modify_links() {
        let mut graph = WikiGraph::new();

        // Create initial page with links to A and B
        graph.update_page(
            "Test",
            PathBuf::from("Test.md"),
            HashMap::new(),
            vec![
                ParsedLink::new("A".to_string(), None),
                ParsedLink::new("B".to_string(), None),
            ],
            SystemTime::now(),
        );

        assert_eq!(graph.get_outlinks("Test").len(), 2);

        // Update: remove link to A, add link to C
        let events = graph.update_page(
            "Test",
            PathBuf::from("Test.md"),
            HashMap::new(),
            vec![
                ParsedLink::new("B".to_string(), None),
                ParsedLink::new("C".to_string(), None),
            ],
            SystemTime::now(),
        );

        // Should have LinkRemoved(A) and LinkCreated(C)
        assert!(events
            .iter()
            .any(|e| matches!(e, GraphEvent::LinkRemoved { from, to } if from == "Test" && to == "A")));
        assert!(events
            .iter()
            .any(|e| matches!(e, GraphEvent::LinkCreated { from, to } if from == "Test" && to == "C")));

        // B should not generate events (unchanged)
        assert!(!events
            .iter()
            .any(|e| matches!(e, GraphEvent::LinkCreated { to, .. } | GraphEvent::LinkRemoved { to, .. } if to == "B")));
    }

    #[test]
    fn test_update_page_no_changes() {
        let mut graph = WikiGraph::new();

        // Create initial page with link to A
        graph.update_page(
            "Test",
            PathBuf::from("Test.md"),
            HashMap::new(),
            vec![ParsedLink::new("A".to_string(), None)],
            SystemTime::now(),
        );

        // Update with same links
        let events = graph.update_page(
            "Test",
            PathBuf::from("Test.md"),
            HashMap::new(),
            vec![ParsedLink::new("A".to_string(), None)],
            SystemTime::now(),
        );

        // No link change events
        assert!(events.is_empty());
    }
}
