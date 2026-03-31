"""Tests for file watching functionality."""

import os
import time
import tempfile
import pytest

from graph_core import GraphEngine


def wait_for_events(engine, timeout=5.0, poll_interval=0.3):
    """Wait for events to appear with timeout."""
    start = time.time()
    while time.time() - start < timeout:
        events = engine.poll_events()
        if events:
            return events
        time.sleep(poll_interval)
    return []


def start_watching_and_wait(engine):
    """Start watching and wait for watcher to initialize."""
    engine.start_watching()
    # Give the watcher thread time to fully initialize
    time.sleep(0.5)
    # Drain any spurious initial events
    engine.poll_events()


@pytest.fixture
def temp_wiki_dir():
    """Create a temporary directory with initial wiki files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create initial file
        with open(os.path.join(tmpdir, "HomePage.md"), "w") as f:
            f.write("# Home\n\nWelcome to [[About]].\n")
        # Let filesystem events settle before tests use this directory
        time.sleep(0.2)
        yield tmpdir


class TestStartStopWatching:
    """Test watcher lifecycle."""

    def test_start_watching(self, temp_wiki_dir):
        """Test starting the file watcher."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()

        engine.start_watching()
        assert engine.is_watching()

        engine.stop_watching()
        assert not engine.is_watching()

    def test_start_watching_twice(self, temp_wiki_dir):
        """Test that starting twice doesn't cause issues."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()

        engine.start_watching()
        engine.start_watching()  # Should stop old and start new
        assert engine.is_watching()

        engine.stop_watching()

    def test_stop_watching_when_not_watching(self, temp_wiki_dir):
        """Test that stopping when not watching is a no-op."""
        engine = GraphEngine(temp_wiki_dir)
        engine.stop_watching()  # Should not raise

    def test_is_watching_initial(self, temp_wiki_dir):
        """Test that is_watching is False initially."""
        engine = GraphEngine(temp_wiki_dir)
        assert not engine.is_watching()


class TestFileCreation:
    """Test events for new file creation."""

    def test_create_new_file(self, temp_wiki_dir):
        """Test that creating a file generates PageCreated event."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            # Create a new file
            new_file = os.path.join(temp_wiki_dir, "NewPage.md")
            with open(new_file, "w") as f:
                f.write("# New Page\n\nContent here.\n")

            events = wait_for_events(engine)

            # Should have a PageCreated event
            created_events = [e for e in events if e.event_type() == "page_created"]
            assert len(created_events) >= 1
            assert created_events[0].page_name() == "NewPage"

            # Page should be in graph
            assert engine.page_exists("NewPage")
        finally:
            engine.stop_watching()

    def test_create_file_with_links(self, temp_wiki_dir):
        """Test that creating a file with links generates link events."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            new_file = os.path.join(temp_wiki_dir, "Linked.md")
            with open(new_file, "w") as f:
                f.write("# Linked\n\nLinks to [[HomePage]] and [[About]].\n")

            events = wait_for_events(engine)

            # Should have PageCreated
            assert any(e.event_type() == "page_created" for e in events)

            # Should have link_created events
            link_events = [e for e in events if e.event_type() == "link_created"]
            assert len(link_events) >= 2
        finally:
            engine.stop_watching()


class TestFileModification:
    """Test events for file modifications."""

    def test_modify_file(self, temp_wiki_dir):
        """Test that modifying a file generates PageUpdated event."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            # Modify HomePage.md
            home_file = os.path.join(temp_wiki_dir, "HomePage.md")
            with open(home_file, "w") as f:
                f.write("# Home Modified\n\nNew content [[Contact]].\n")

            events = wait_for_events(engine)

            # Should have PageUpdated
            update_events = [e for e in events if e.event_type() == "page_updated"]
            assert len(update_events) >= 1
            assert update_events[0].page_name() == "HomePage"
        finally:
            engine.stop_watching()

    def test_modify_links(self, temp_wiki_dir):
        """Test that changing links generates link events."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            # Original links to About, change to link to Contact
            home_file = os.path.join(temp_wiki_dir, "HomePage.md")
            with open(home_file, "w") as f:
                f.write("# Home\n\nNow links to [[Contact]] only.\n")

            events = wait_for_events(engine)

            # Should have link_removed (About) and link_created (Contact)
            removed = [e for e in events if e.event_type() == "link_removed"]
            created = [e for e in events if e.event_type() == "link_created"]

            assert len(removed) >= 1
            assert len(created) >= 1
        finally:
            engine.stop_watching()


class TestFileDeletion:
    """Test events for file deletions."""

    def test_delete_file(self, temp_wiki_dir):
        """Test that deleting a file generates PageDeleted event."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            # Delete HomePage.md
            home_file = os.path.join(temp_wiki_dir, "HomePage.md")
            os.remove(home_file)

            events = wait_for_events(engine)

            # Should have PageDeleted
            delete_events = [e for e in events if e.event_type() == "page_deleted"]
            assert len(delete_events) >= 1
            assert delete_events[0].page_name() == "HomePage"
        finally:
            engine.stop_watching()


class TestNonMarkdownFiles:
    """Test that non-.md files are ignored."""

    def test_ignore_txt_files(self, temp_wiki_dir):
        """Test that .txt files don't generate events."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        engine.start_watching()

        try:
            # Create a .txt file
            txt_file = os.path.join(temp_wiki_dir, "notes.txt")
            with open(txt_file, "w") as f:
                f.write("Some notes\n")

            time.sleep(1.0)

            events = engine.poll_events()

            # Should have no page events for .txt file
            page_events = [e for e in events if e.page_name() == "notes"]
            assert len(page_events) == 0
        finally:
            engine.stop_watching()


class TestPollEvents:
    """Test poll_events behavior."""

    def test_poll_clears_queue(self, temp_wiki_dir):
        """Test that polling clears the event queue."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            # Create a file
            new_file = os.path.join(temp_wiki_dir, "Test.md")
            with open(new_file, "w") as f:
                f.write("# Test\n")

            # First poll gets events
            events1 = wait_for_events(engine)
            assert len(events1) >= 1

            # Second poll should be empty
            events2 = engine.poll_events()
            assert len(events2) == 0
        finally:
            engine.stop_watching()

    def test_has_pending_events(self, temp_wiki_dir):
        """Test has_pending_events method."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            assert not engine.has_pending_events()

            # Create a file
            new_file = os.path.join(temp_wiki_dir, "Test.md")
            with open(new_file, "w") as f:
                f.write("# Test\n")

            # Wait for events to appear
            events = wait_for_events(engine)
            # We already consumed them, but verify we got some
            assert len(events) >= 1

            assert not engine.has_pending_events()
        finally:
            engine.stop_watching()


class TestGraphEventMethods:
    """Test GraphEvent methods."""

    def test_event_type(self, temp_wiki_dir):
        """Test event_type() method."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            new_file = os.path.join(temp_wiki_dir, "Test.md")
            with open(new_file, "w") as f:
                f.write("# Test\n")

            events = wait_for_events(engine)
            assert len(events) >= 1

            event = events[0]
            assert event.event_type() in [
                "page_created",
                "page_updated",
                "page_deleted",
                "link_created",
                "link_removed",
            ]
        finally:
            engine.stop_watching()

    def test_page_name(self, temp_wiki_dir):
        """Test page_name() method for page events."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            new_file = os.path.join(temp_wiki_dir, "TestPage.md")
            with open(new_file, "w") as f:
                f.write("# Test\n")

            events = wait_for_events(engine)
            page_events = [e for e in events if e.event_type() == "page_created"]

            assert len(page_events) >= 1
            assert page_events[0].page_name() == "TestPage"
        finally:
            engine.stop_watching()

    def test_link_from_to(self, temp_wiki_dir):
        """Test link_from() and link_to() methods for link events."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            new_file = os.path.join(temp_wiki_dir, "Source.md")
            with open(new_file, "w") as f:
                f.write("# Source\n\nLink to [[Target]].\n")

            events = wait_for_events(engine)
            link_events = [e for e in events if e.event_type() == "link_created"]

            assert len(link_events) >= 1
            link_event = link_events[0]
            assert link_event.link_from() == "Source"
            assert link_event.link_to() == "Target"

            # Page events should have None for link methods
            page_events = [e for e in events if e.event_type() == "page_created"]
            if page_events:
                assert page_events[0].link_from() is None
                assert page_events[0].link_to() is None
        finally:
            engine.stop_watching()

    def test_event_repr(self, temp_wiki_dir):
        """Test __repr__ for GraphEvent."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        start_watching_and_wait(engine)

        try:
            new_file = os.path.join(temp_wiki_dir, "Test.md")
            with open(new_file, "w") as f:
                f.write("# Test\n")

            events = wait_for_events(engine)
            assert len(events) >= 1

            repr_str = repr(events[0])
            assert "GraphEvent" in repr_str
        finally:
            engine.stop_watching()


class TestRebuildWithWatching:
    """Test rebuild behavior with watching."""

    def test_rebuild_restarts_watcher(self, temp_wiki_dir):
        """Test that rebuild restarts watcher if it was running."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()
        engine.start_watching()

        assert engine.is_watching()

        # Rebuild should temporarily stop and restart watcher
        engine.rebuild()

        assert engine.is_watching()

        engine.stop_watching()

    def test_rebuild_without_watcher(self, temp_wiki_dir):
        """Test that rebuild works without watcher."""
        engine = GraphEngine(temp_wiki_dir)
        engine.rebuild()

        assert not engine.is_watching()

        engine.rebuild()

        assert not engine.is_watching()
