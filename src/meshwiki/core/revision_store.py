"""SQLite-backed revision store for wiki page history."""

import difflib
import sqlite3
from datetime import datetime
from pathlib import Path

from meshwiki.core.models import Revision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS revisions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    page_name TEXT    NOT NULL,
    revision  INTEGER NOT NULL,
    timestamp TEXT    NOT NULL,
    content   TEXT    NOT NULL,
    message   TEXT    NOT NULL DEFAULT '',
    author    TEXT    NOT NULL DEFAULT '',
    operation TEXT    NOT NULL DEFAULT 'edit',
    UNIQUE(page_name, revision)
);
CREATE INDEX IF NOT EXISTS idx_revisions_page
    ON revisions(page_name, revision DESC);
CREATE INDEX IF NOT EXISTS idx_revisions_timestamp
    ON revisions(timestamp DESC);
"""


def _row_to_revision(row: sqlite3.Row) -> Revision:
    return Revision(
        id=row["id"],
        page_name=row["page_name"],
        revision=row["revision"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        content=row["content"],
        message=row["message"],
        author=row["author"],
        operation=row["operation"],
    )


class RevisionStore:
    """Stores per-page revision history in a SQLite database.

    The database lives alongside the wiki pages directory as a hidden file
    (``.revisions.db``) so it is covered by the Docker bind mount.  The Rust
    file watcher ignores non-``.md`` files, so the database is invisible to it.
    """

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def record(
        self,
        page_name: str,
        content: str,
        operation: str = "edit",
        message: str = "",
        author: str = "",
    ) -> int:
        """Store a new revision.  Returns the new per-page revision number."""
        with self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM revisions WHERE page_name = ?",
                (page_name,),
            ).fetchone()
            next_rev = row[0] + 1
            self._conn.execute(
                """
                INSERT INTO revisions (page_name, revision, timestamp, content, message, author, operation)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_name,
                    next_rev,
                    datetime.now().isoformat(),
                    content,
                    message,
                    author,
                    operation,
                ),
            )
        return next_rev

    def delete_page_history(self, page_name: str) -> None:
        """Remove all revisions for a page (called when the page is deleted)."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM revisions WHERE page_name = ?", (page_name,)
            )

    def rename_history(self, old_name: str, new_name: str) -> None:
        """Re-attribute all revisions from old_name to new_name."""
        with self._conn:
            self._conn.execute(
                "UPDATE revisions SET page_name = ? WHERE page_name = ?",
                (new_name, old_name),
            )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_revision(self, page_name: str, revision: int) -> Revision | None:
        """Fetch a specific revision by number."""
        row = self._conn.execute(
            "SELECT * FROM revisions WHERE page_name = ? AND revision = ?",
            (page_name, revision),
        ).fetchone()
        return _row_to_revision(row) if row else None

    def get_latest_revision(self, page_name: str) -> Revision | None:
        """Fetch the most recent revision for a page."""
        row = self._conn.execute(
            "SELECT * FROM revisions WHERE page_name = ? ORDER BY revision DESC LIMIT 1",
            (page_name,),
        ).fetchone()
        return _row_to_revision(row) if row else None

    def list_revisions(
        self, page_name: str, limit: int = 50, offset: int = 0
    ) -> list[Revision]:
        """List revisions for a page, newest first."""
        rows = self._conn.execute(
            """
            SELECT * FROM revisions
            WHERE page_name = ?
            ORDER BY revision DESC
            LIMIT ? OFFSET ?
            """,
            (page_name, limit, offset),
        ).fetchall()
        return [_row_to_revision(r) for r in rows]

    def revision_count(self, page_name: str) -> int:
        """Count of revisions for a page."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM revisions WHERE page_name = ?", (page_name,)
        ).fetchone()
        return row[0]

    def diff_revisions(self, page_name: str, rev_a: int, rev_b: int) -> list[dict]:
        """Return a line-by-line diff between two revisions.

        Returns a list of dicts with keys:
          - ``tag``: one of ``"equal"``, ``"insert"``, ``"delete"``, ``"replace"``
          - ``old_line``: line number in rev_a (or None for inserts)
          - ``new_line``: line number in rev_b (or None for deletes)
          - ``content``: the line text
        """
        rev_a_obj = self.get_revision(page_name, rev_a)
        rev_b_obj = self.get_revision(page_name, rev_b)
        if rev_a_obj is None or rev_b_obj is None:
            return []

        lines_a = rev_a_obj.content.splitlines(keepends=True)
        lines_b = rev_b_obj.content.splitlines(keepends=True)

        diff: list[dict] = []
        matcher = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k, line in enumerate(lines_a[i1:i2]):
                    diff.append(
                        {
                            "tag": "equal",
                            "old_line": i1 + k + 1,
                            "new_line": j1 + k + 1,
                            "content": line.rstrip("\n"),
                        }
                    )
            elif tag == "insert":
                for k, line in enumerate(lines_b[j1:j2]):
                    diff.append(
                        {
                            "tag": "insert",
                            "old_line": None,
                            "new_line": j1 + k + 1,
                            "content": line.rstrip("\n"),
                        }
                    )
            elif tag == "delete":
                for k, line in enumerate(lines_a[i1:i2]):
                    diff.append(
                        {
                            "tag": "delete",
                            "old_line": i1 + k + 1,
                            "new_line": None,
                            "content": line.rstrip("\n"),
                        }
                    )
            elif tag == "replace":
                for k, line in enumerate(lines_a[i1:i2]):
                    diff.append(
                        {
                            "tag": "delete",
                            "old_line": i1 + k + 1,
                            "new_line": None,
                            "content": line.rstrip("\n"),
                        }
                    )
                for k, line in enumerate(lines_b[j1:j2]):
                    diff.append(
                        {
                            "tag": "insert",
                            "old_line": None,
                            "new_line": j1 + k + 1,
                            "content": line.rstrip("\n"),
                        }
                    )
        return diff

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
