"""Per-role/per-stage model routing and the new engine spec factories."""
from __future__ import annotations

import sqlite3
import unittest

from engine.config import ConfigError, RoleDef, TriageConfig
from engine.engine import TriageEngine
from engine.item_vault import slugify
from engine.kanban_store import KanbanStore
from tests.pipeline_fixtures import BOARD_SCHEMA_SQL, pipeline_config_data


def load_config(**overrides) -> TriageConfig:
    data = pipeline_config_data()
    data.update(overrides)
    data["hermes"] = None or data["hermes"]
    return TriageConfig.from_dict(data, config_path="triage.yaml")


class ConfigModelRoutingTests(unittest.TestCase):
    def test_string_and_mapping_role_forms(self):
        cfg = load_config()
        self.assertEqual(cfg.role_def("orchestrator"), RoleDef(profile="orchestrator"))
        self.assertEqual(
            cfg.role_def("researcher"),
            RoleDef(profile="researcher", model="cheap-model-1", reasoning_effort="low"),
        )
        self.assertEqual(cfg.role_to_profile("researcher"), "researcher")

    def test_mapping_role_requires_profile(self):
        data = pipeline_config_data()
        data["roles"]["researcher"] = {"model": "m"}
        with self.assertRaises(ConfigError):
            TriageConfig.from_dict(data, config_path="triage.yaml")

    def test_mapping_role_rejects_unknown_keys(self):
        data = pipeline_config_data()
        data["roles"]["researcher"] = {"profile": "researcher", "modle": "typo"}
        with self.assertRaises(ConfigError):
            TriageConfig.from_dict(data, config_path="triage.yaml")

    def test_path_deliverable_is_parsed(self):
        cfg = load_config()
        self.assertEqual(cfg.get_path("build").deliverable, "deliverable.md")


class EngineSpecTests(unittest.TestCase):
    def setUp(self):
        self.engine = TriageEngine(load_config())

    def test_research_specs_carry_role_model_line(self):
        specs = self.engine.research_specs("slug-a", "t_root")
        self.assertEqual(len(specs), 2)
        for spec in specs:
            self.assertEqual(spec.model_override, "cheap-model-1")
            self.assertEqual(spec.reasoning_effort, "low")

    def test_stage_model_wins_over_role(self):
        specs = self.engine.fulfillment_specs("slug-a", "build")
        by_stage = {s.title.split(":")[0]: s for s in specs}
        self.assertEqual(by_stage["do_build"].model_override, "cheap-model-1")   # role-level
        self.assertEqual(by_stage["review"].model_override, "frontier-model-x")  # stage-level wins
        self.assertIsNone(by_stage["final_delivery"].model_override)

    def test_final_fulfill_stage_gets_delivery_instruction(self):
        specs = self.engine.fulfillment_specs("slug-a", "build")
        self.assertIn("FINAL DELIVERY", specs[-1].body)
        self.assertIn("delivery_actions.py", specs[-1].body)
        self.assertIn("slug-a", specs[-1].body)
        # The first (artifact-producing) stage gets only the dry-run layout check;
        # the real send instruction stays exclusive to the final stage.
        self.assertIn("deliver slug-a --dry-run", specs[0].body)
        self.assertNotIn("FINAL DELIVERY", specs[0].body)
        for spec in specs[1:-1]:
            self.assertNotIn("delivery_actions.py", spec.body)

    def test_triage_root_and_route_specs(self):
        root = self.engine.triage_root_spec("slug-a", "t_intake")
        self.assertEqual(root.parents, ["t_intake"])
        self.assertIn("intake_actions.py", root.body)
        self.assertIn("verify --slug slug-a", root.body)
        route = self.engine.route_spec("slug-a", "t_classifier")
        self.assertEqual(route.parents, ["t_classifier"])
        self.assertEqual(route.skills, ["triage-synth"])
        self.assertIn("pre_gate_actions.py", route.body)
        self.assertIn("--classification", route.body)

    def test_slugify_is_canonical(self):
        self.assertEqual(slugify("Build a Widget (v2) — Frobnicator!"), "build-a-widget-frobnicator")
        self.assertEqual(slugify("(all parenthetical)"), "untitled")


class StoreOptionalColumnTests(unittest.TestCase):
    def _store_and_conn(self, schema: str):
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema)
        store = KanbanStore.__new__(KanbanStore)
        return store, conn

    def test_writes_optional_columns_when_present(self):
        store, conn = self._store_and_conn(BOARD_SCHEMA_SQL)
        task_id = store.create_task(
            conn, title="t", body="b", assignee="a",
            idempotency_key="k1", skills=["skill-x"],
            model_override="m1", provider_override="p1", reasoning_effort="low",
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        cols = [d[0] for d in conn.execute("SELECT * FROM tasks LIMIT 1").description]
        record = dict(zip(cols, row))
        self.assertEqual(record["idempotency_key"], "k1")
        self.assertEqual(record["model_override"], "m1")
        self.assertEqual(record["provider_override"], "p1")
        self.assertEqual(record["reasoning_effort"], "low")
        self.assertIn("skill-x", record["skills"])

    def test_tolerates_minimal_legacy_schema(self):
        store, conn = self._store_and_conn("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT,
                status TEXT, priority INTEGER, created_by TEXT, created_at INTEGER,
                workspace_kind TEXT, workspace_path TEXT, consecutive_failures INTEGER);
            CREATE TABLE task_links (parent_id TEXT, child_id TEXT, UNIQUE(parent_id, child_id));
            CREATE TABLE task_events (task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
        """)
        task_id = store.create_task(
            conn, title="t", body="b", assignee="a",
            idempotency_key="k1", model_override="ignored-safely",
        )
        self.assertTrue(task_id.startswith("t_"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_key_lookup_finds_cli_created_card_and_skips_archived(self):
        store, conn = self._store_and_conn(BOARD_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, priority, created_by,"
            " created_at, workspace_kind, consecutive_failures, idempotency_key)"
            " VALUES ('t_native', 't', 'b', 'a', 'ready', 0, 'cli', 0, 'scratch', 0, 'k1')"
        )
        self.assertEqual(
            store.create_task(conn, title="t", body="b", assignee="a", idempotency_key="k1"),
            "t_native",
        )
        conn.execute("UPDATE tasks SET status = 'archived' WHERE id = 't_native'")
        fresh = store.create_task(conn, title="t", body="b", assignee="a", idempotency_key="k1")
        self.assertNotEqual(fresh, "t_native")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
