//! Data models for the MeshWiki graph engine.
//!
//! This module defines the core data structures used to represent
//! wiki pages and the links between them.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::SystemTime;

/// Represents a wiki page node in the graph.
///
/// Contains all metadata about a wiki page including its name,
/// file path, frontmatter metadata, and modification time.
#[derive(Clone, Debug)]
pub struct PageNode {
    /// The page name (derived from filename without .md extension)
    pub name: String,

    /// The file path relative to the wiki data directory
    pub file_path: PathBuf,

    /// Frontmatter metadata as key-value pairs.
    /// Values are vectors to support multi-value fields (e.g., tags: [a, b, c])
    pub metadata: HashMap<String, Vec<String>>,

    /// Last modification time of the file
    pub last_modified: SystemTime,

    /// Whether this node corresponds to a real page backed by a file on disk.
    ///
    /// `false` means the node is a link-only stub, created because another
    /// page links to it (e.g. `[[MissingPage]]`) even though no file exists.
    /// `page_exists` and page listings must treat stubs as non-existent so
    /// missing-link styling and page counts stay correct.
    pub exists: bool,
}

impl PageNode {
    /// Create a new real PageNode with the given name and file path.
    ///
    /// Metadata is initialized as empty, and last_modified is set to now.
    /// The node is marked as existing (backed by a real page).
    pub fn new(name: String, file_path: PathBuf) -> Self {
        Self {
            name,
            file_path,
            metadata: HashMap::new(),
            last_modified: SystemTime::now(),
            exists: true,
        }
    }

    /// Create a link-only stub PageNode for a target that has no file yet.
    ///
    /// The node is marked as non-existent (`exists = false`) so it does not
    /// count as a real page.
    pub fn new_stub(name: String, file_path: PathBuf) -> Self {
        Self {
            name,
            file_path,
            metadata: HashMap::new(),
            last_modified: SystemTime::now(),
            exists: false,
        }
    }

    /// Create a new real PageNode with metadata.
    pub fn with_metadata(
        name: String,
        file_path: PathBuf,
        metadata: HashMap<String, Vec<String>>,
        last_modified: SystemTime,
    ) -> Self {
        Self {
            name,
            file_path,
            metadata,
            last_modified,
            exists: true,
        }
    }
}

/// Represents a wiki link (edge) in the graph.
///
/// Wiki links can have an optional display text that differs from
/// the target page name: `[[PageName|Display Text]]`
#[derive(Clone, Debug, Default)]
pub struct WikiLink {
    /// Optional display text for the link.
    /// None means the link is displayed as the page name itself.
    pub display_text: Option<String>,
}

impl WikiLink {
    /// Create a new WikiLink without display text.
    pub fn new() -> Self {
        Self { display_text: None }
    }

    /// Create a new WikiLink with display text.
    pub fn with_display_text(text: String) -> Self {
        Self {
            display_text: Some(text),
        }
    }
}

/// A parsed wiki link extracted from markdown content.
///
/// This is used during parsing to collect links before adding them to the graph.
#[derive(Clone, Debug, PartialEq)]
pub struct ParsedLink {
    /// The target page name
    pub target: String,

    /// Optional display text
    pub display_text: Option<String>,
}

impl ParsedLink {
    /// Create a new ParsedLink.
    pub fn new(target: String, display_text: Option<String>) -> Self {
        Self {
            target,
            display_text,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_page_node_new() {
        let node = PageNode::new("TestPage".to_string(), PathBuf::from("TestPage.md"));
        assert_eq!(node.name, "TestPage");
        assert_eq!(node.file_path, PathBuf::from("TestPage.md"));
        assert!(node.metadata.is_empty());
    }

    #[test]
    fn test_page_node_with_metadata() {
        let mut metadata = HashMap::new();
        metadata.insert("status".to_string(), vec!["draft".to_string()]);
        metadata.insert(
            "tags".to_string(),
            vec!["rust".to_string(), "wiki".to_string()],
        );

        let node = PageNode::with_metadata(
            "TestPage".to_string(),
            PathBuf::from("TestPage.md"),
            metadata,
            SystemTime::now(),
        );

        assert_eq!(node.name, "TestPage");
        assert_eq!(
            node.metadata.get("status"),
            Some(&vec!["draft".to_string()])
        );
        assert_eq!(
            node.metadata.get("tags"),
            Some(&vec!["rust".to_string(), "wiki".to_string()])
        );
    }

    #[test]
    fn test_wiki_link_new() {
        let link = WikiLink::new();
        assert!(link.display_text.is_none());
    }

    #[test]
    fn test_wiki_link_with_display_text() {
        let link = WikiLink::with_display_text("Custom Text".to_string());
        assert_eq!(link.display_text, Some("Custom Text".to_string()));
    }

    #[test]
    fn test_parsed_link() {
        let link = ParsedLink::new("TargetPage".to_string(), Some("Display".to_string()));
        assert_eq!(link.target, "TargetPage");
        assert_eq!(link.display_text, Some("Display".to_string()));
    }
}
