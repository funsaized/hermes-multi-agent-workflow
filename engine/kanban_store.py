"""Thin writer over the Hermes Kanban SQLite board.

The Kanban board is the inter-agent bus. Edge adapters such as
`intake_actions.py`, `pre_gate_actions.py`, and `proposal_actions.py` create
cards from engine task specs; the Hermes gateway's dispatcher claims `ready`
cards and spawns the assigned agent. Parent links drive fan-in: a child task
stays `todo` until every parent is `done`, then Hermes promotes it to `ready`.

This is generic Hermes plumbing — you should not need to edit it. The only
domain-ish constant is `created_by`, a free-text provenance tag.

Board location: `resolve_board_db()` mirrors Hermes's own resolution. The
dispatcher injects HERMES_KANBAN_DB into workers (that always wins); otherwise
the board lives under the Hermes home, which is `$HERMES_HOME` when set, else
the first existing of `~/.hermes` and `%LOCALAPPDATA%/hermes` (Windows
installs use the latter). Never hardcode a board path in a script — that is
how per-machine paths leak into task bodies.

Idempotency: `create_task` is interoperable with cards created through the
`hermes kanban` CLI. When the board schema has an `idempotency_key` column,
the store first looks up an existing NON-ARCHIVED card by key (the native
Hermes contract) and returns its id; only then does it fall back to the
deterministic hashed id. Archived cards never satisfy a key — history is
evidence, not an active graph.

NOTE: this writes the Hermes board schema directly. It depends on the core
columns in `tasks` plus the `task_links`, `task_comments`, and `task_events`
tables, and OPTIONALLY writes `idempotency_key`, `skills`, `model_override`,
`provider_override`, and `reasoning_effort` when the installed schema has
them. If a future Hermes release changes the core columns, update this
adapter. See `docs/02-the-board.md`.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Columns written only when the installed board schema has them.
OPTIONAL_TASK_COLUMNS = (
    "idempotency_key",
    "skills",
    "model_override",
    "provider_override",
    "reasoning_effort",
)


def utc_now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def hermes_home() -> Path:
    """Resolve the Hermes home directory the way the installed CLI does.

    $HERMES_HOME wins; otherwise probe the conventional locations and return
    the first that exists (macOS/Linux `~/.hermes`, Windows `%LOCALAPPDATA%/hermes`).
    Falls back to `~/.hermes` so error messages stay conventional.
    """
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).resolve()
    candidates = [Path.home() / ".hermes"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def resolve_board_db(board: str) -> Path:
    """Resolve a board's DB path.

    The dispatcher-injected HERMES_KANBAN_DB always wins. Otherwise: the
    back-compat `default` board is `<home>/kanban.db`; a named board lives at
    `<home>/kanban/boards/<slug>/kanban.db`. When several Hermes homes exist,
    prefer the one that actually contains this board.
    """
    env = os.environ.get("HERMES_KANBAN_DB")
    if env:
        return Path(env).resolve()

    def db_under(home: Path) -> Path:
        if board == "default":
            return home / "kanban.db"
        return home / "kanban" / "boards" / board / "kanban.db"

    homes = [hermes_home()]
    local = os.environ.get("LOCALAPPDATA")
    for extra in (Path.home() / ".hermes", *( [Path(local) / "hermes"] if local else [] )):
        if extra not in homes:
            homes.append(extra)
    for home in homes:
        candidate = db_under(home)
        if candidate.exists():
            return candidate
    return db_under(homes[0])


class KanbanStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve()
        self._columns_cache: set[str] | None = None

    def connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Kanban DB not found: {self.db_path}. "
                "Check HERMES_KANBAN_DB / HERMES_HOME and that the board exists."
            )
        conn = sqlite3.connect(str(self.db_path), isolation_level="DEFERRED")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def new_task_id() -> str:
        return "t_" + secrets.token_hex(4)

    def _task_columns(self, conn: sqlite3.Connection) -> set[str]:
        # getattr: tests may construct the store without __init__.
        cache = getattr(self, "_columns_cache", None)
        if cache is None:
            cache = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            self._columns_cache = cache
        return cache

    def find_by_idempotency_key(self, conn: sqlite3.Connection, key: str) -> str | None:
        """Return the id of an existing ACTIVE (non-archived) card with this key.

        Interoperates with cards created through `hermes kanban ... --idempotency-key`,
        whose ids are Hermes-native and unrelated to this adapter's hashed ids.
        """
        if "idempotency_key" not in self._task_columns(conn):
            return None
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived'",
            (key,),
        ).fetchone()
        return row[0] if row else None

    def create_task(
        self,
        conn: sqlite3.Connection,
        *,
        title: str,
        body: str,
        assignee: str,
        parents: list[str] | None = None,
        created_by: str = "hermes-multi-agent-workflow",
        workspace_kind: str = "scratch",
        workspace_path: str | None = None,
        idempotency_key: str | None = None,
        skills: list[str] | None = None,
        model_override: str | None = None,
        provider_override: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        columns = self._task_columns(conn)
        if idempotency_key:
            existing = self.find_by_idempotency_key(conn, idempotency_key)
            if existing:
                return existing
            task_id = "t_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:8]
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is not None:
                # Same hashed id already on the board. Active → idempotent return.
                # Archived → history never satisfies a key; mint a fresh id.
                if row[0] != "archived":
                    return task_id
                task_id = self.new_task_id()
        else:
            task_id = self.new_task_id()

        now = utc_now_epoch()
        # No parents → `ready` (runs now). With parents → `todo` until they finish.
        status = "todo" if parents else "ready"
        fields: dict[str, Any] = {
            "id": task_id,
            "title": title,
            "body": body,
            "assignee": assignee,
            "status": status,
            "priority": 0,
            "created_by": created_by,
            "created_at": now,
            "workspace_kind": workspace_kind,
            "workspace_path": workspace_path,
            "consecutive_failures": 0,
        }
        optional_values: dict[str, Any] = {
            "idempotency_key": idempotency_key,
            "skills": json.dumps(skills) if skills else None,
            "model_override": model_override,
            "provider_override": provider_override,
            "reasoning_effort": reasoning_effort,
        }
        for column in OPTIONAL_TASK_COLUMNS:
            if column in columns and optional_values[column] is not None:
                fields[column] = optional_values[column]
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO tasks ({', '.join(fields)}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        if parents:
            for parent in parents:
                conn.execute(
                    "INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?, ?)",
                    (parent, task_id),
                )
        self.append_event(conn, task_id, "created", {"assignee": assignee, "parents": parents or []})
        return task_id

    # ----- read helpers (verification, never mutation) ----- #

    def get_task(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def find_active_by_title(self, conn: sqlite3.Connection, title: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM tasks WHERE title = ? AND status != 'archived' ORDER BY created_at DESC",
            (title,),
        ).fetchone()

    def parent_ids(self, conn: sqlite3.Connection, task_id: str) -> list[str]:
        return [
            row["parent_id"]
            for row in conn.execute(
                "SELECT parent_id FROM task_links WHERE child_id = ?", (task_id,)
            )
        ]

    def child_ids(self, conn: sqlite3.Connection, task_id: str) -> list[str]:
        return [
            row["child_id"]
            for row in conn.execute(
                "SELECT child_id FROM task_links WHERE parent_id = ?", (task_id,)
            )
        ]

    def comment(self, conn: sqlite3.Connection, task_id: str, *, author: str, body: str) -> None:
        conn.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (task_id, author, body, utc_now_epoch()),
        )
        self.append_event(conn, task_id, "comment_added", {"author": author})

    def close_task(self, conn: sqlite3.Connection, task_id: str, *, outcome: str, summary: str) -> None:
        now = utc_now_epoch()
        conn.execute("UPDATE tasks SET status = 'done', completed_at = ?, result = ? WHERE id = ?", (now, summary, task_id))
        self.append_event(conn, task_id, "done", {"outcome": outcome, "summary": summary})

    def append_event(self, conn: sqlite3.Connection, task_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (task_id, kind, json.dumps(payload or {}), utc_now_epoch()),
        )
