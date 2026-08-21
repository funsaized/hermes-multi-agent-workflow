import sqlite3
import unittest
from unittest.mock import Mock, patch

import pre_gate_actions
from engine.engine import TaskSpec
from engine.kanban_store import KanbanStore


class PreGateActionsTests(unittest.TestCase):
    def test_resolves_roles_and_builds_chain(self):
        item = Mock(frontmatter={"path": "course", "linked_kanban_tasks": []})
        vault, engine, store, conn = Mock(), Mock(), Mock(), Mock()
        vault.load.return_value = item
        engine.prep_specs.return_value = [
            TaskSpec("one: item", "one", "curriculum_analyst"),
            TaskSpec("two: item", "two", "course_author"),
        ]
        store.connect.return_value = conn
        store.create_task.side_effect = ["t_one", "t_two", "t_propose"]
        path = Mock(propose_role="orchestrator", proposal_template="proposal.md")
        config = Mock(paths={"course": path}, workspace_root=".", workspace_path=pre_gate_actions.Path("work"), pipeline_id="test")
        config.hermes.project_root = "."
        config.get_path.return_value = path
        config.role_to_profile.side_effect = {"curriculum_analyst": "analyst", "course_author": "author", "orchestrator": "default"}.__getitem__
        config.gate.target = "discord:123"

        with (
            patch.object(pre_gate_actions.TriageConfig, "load", return_value=config),
            patch.object(pre_gate_actions, "ItemVault", return_value=vault),
            patch.object(pre_gate_actions, "TriageEngine", return_value=engine),
            patch.object(pre_gate_actions, "KanbanStore", return_value=store),
            patch.object(pre_gate_actions, "board_db"),
        ):
            result = pre_gate_actions.apply_prep("item", "t_route")

        calls = store.create_task.call_args_list
        self.assertEqual(calls[0].kwargs["assignee"], "analyst")
        self.assertEqual(calls[0].kwargs["parents"], ["t_route"])
        self.assertEqual(calls[1].kwargs["assignee"], "author")
        self.assertEqual(calls[1].kwargs["parents"], ["t_one"])
        self.assertEqual(calls[2].kwargs["assignee"], "default")
        self.assertEqual(calls[2].kwargs["parents"], ["t_two"])
        self.assertIn("approve test:item", calls[2].kwargs["body"])
        self.assertEqual(result["proposal_task_id"], "t_propose")
        self.assertEqual(item.frontmatter["linked_kanban_tasks"], ["t_one", "t_two", "t_propose"])

    def test_store_idempotency_key_returns_existing_task(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT,
                status TEXT, priority INTEGER, created_by TEXT, created_at INTEGER,
                workspace_kind TEXT, workspace_path TEXT, consecutive_failures INTEGER);
            CREATE TABLE task_links (parent_id TEXT, child_id TEXT, UNIQUE(parent_id, child_id));
            CREATE TABLE task_events (task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
        """)
        store = KanbanStore.__new__(KanbanStore)
        kwargs = dict(title="one", body="body", assignee="analyst", idempotency_key="same")
        first = store.create_task(conn, **kwargs)
        second = store.create_task(conn, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
