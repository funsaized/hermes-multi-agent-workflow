"""intake_actions adapter tests — plan/apply/verify against a live-schema board."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import intake_actions
from tests.pipeline_fixtures import make_pipeline, write_intake_report

SCORES = {
    "scores": {
        "build-a-widget-frobnicator": {"frequency": 25, "intensity": 25},
        "minor-cosmetic-nit": {"frequency": 2, "intensity": 3},
    }
}


class IntakeActionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="intake actions ")
        self.root = Path(self._tmp.name)
        self.config_path, self.db_path = make_pipeline(self.root)
        self.report = write_intake_report(self.root)
        self.scores_path = self.root / "scores.json"
        self.scores_path.write_text(json.dumps(SCORES), encoding="utf-8")
        self._env = patch.dict(os.environ, {"HERMES_KANBAN_DB": str(self.db_path)})
        self._env.start()
        os.environ.pop("TRIAGE_VAULT_DIR", None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _apply(self):
        return intake_actions.action_apply(
            str(self.report), str(self.scores_path), "t_intake1", self.config_path
        )

    def _rows(self, sql, *args):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def test_plan_is_read_only_and_reports_slugs_and_rubric(self):
        result = intake_actions.action_plan(str(self.report), self.config_path)
        self.assertTrue(result["ok"])
        slugs = [c["slug"] for c in result["candidates"]]
        self.assertEqual(slugs, [
            "build-a-widget-frobnicator", "minor-cosmetic-nit", "build-the-widget-frobnicator",
        ])
        self.assertTrue(all(c["needs_score"] for c in result["candidates"]))
        self.assertIn("frequency (0..25)", result["rubric_prompt"])
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM tasks")[0]["n"], 0)

    def test_apply_builds_graph_shelves_and_merges(self):
        result = self._apply()
        self.assertTrue(result["ok"])
        by_slug = {d["slug"]: d for d in result["decisions"]}

        advancing = by_slug["build-a-widget-frobnicator"]
        self.assertEqual(advancing["action"], "graph_created")
        self.assertEqual(by_slug["minor-cosmetic-nit"]["action"], "shelved_below_threshold")
        # Third candidate dedups against the first one's freshly created item.
        merged = by_slug["build-the-widget-frobnicator"]
        self.assertEqual(merged["action"], "merged_duplicate")
        self.assertEqual(merged["into"], "build-a-widget-frobnicator")

        root = self._rows("SELECT * FROM tasks WHERE id = ?", advancing["triage_task_id"])[0]
        self.assertEqual(root["status"], "todo")  # parented to the intake task
        links = self._rows("SELECT parent_id FROM task_links WHERE child_id = ?", root["id"])
        self.assertEqual([l["parent_id"] for l in links], ["t_intake1"])

        # Two evidence lanes (classify is the classifier), researcher model line applied.
        lanes = self._rows(
            "SELECT * FROM tasks WHERE id IN (%s)"
            % ",".join("?" * len(advancing["lane_task_ids"])),
            *advancing["lane_task_ids"],
        )
        self.assertEqual(len(lanes), 2)
        for lane in lanes:
            self.assertEqual(lane["assignee"], "researcher")
            self.assertEqual(lane["model_override"], "cheap-model-1")
            self.assertEqual(lane["reasoning_effort"], "low")

        classifier_parents = {
            l["parent_id"] for l in self._rows(
                "SELECT parent_id FROM task_links WHERE child_id = ?", advancing["classifier_task_id"]
            )
        }
        self.assertEqual(classifier_parents, set(advancing["lane_task_ids"]))
        route = self._rows("SELECT * FROM tasks WHERE id = ?", advancing["route_task_id"])[0]
        self.assertEqual(json.loads(route["skills"]), ["triage-synth"])
        route_parents = self._rows("SELECT parent_id FROM task_links WHERE child_id = ?", route["id"])
        self.assertEqual([l["parent_id"] for l in route_parents], [advancing["classifier_task_id"]])

    def test_apply_is_idempotent(self):
        first = self._apply()
        count_first = self._rows("SELECT COUNT(*) AS n FROM tasks")[0]["n"]
        second = self._apply()
        self.assertTrue(second["ok"])
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM tasks")[0]["n"], count_first)
        firsts = {d["slug"]: d for d in first["decisions"]}
        seconds = {d["slug"]: d for d in second["decisions"]}
        self.assertEqual(
            firsts["build-a-widget-frobnicator"]["triage_task_id"],
            seconds["build-a-widget-frobnicator"]["triage_task_id"],
        )

    def test_apply_reuses_cli_created_card_with_same_idempotency_key(self):
        # A card created through `hermes kanban` has a native id but the same
        # key in the idempotency_key column; the adapter must find and reuse it.
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, priority, created_by,"
            " created_at, workspace_kind, consecutive_failures, idempotency_key)"
            " VALUES ('t_native99', 'triage: build-a-widget-frobnicator', 'b', 'orchestrator',"
            " 'todo', 0, 'cli', 0, 'scratch', 0, 'synth:triage:t_intake1:build-a-widget-frobnicator')"
        )
        conn.commit()
        conn.close()
        result = self._apply()
        advancing = {d["slug"]: d for d in result["decisions"]}["build-a-widget-frobnicator"]
        self.assertEqual(advancing["triage_task_id"], "t_native99")

    def test_apply_without_scores_reports_needs_score(self):
        empty = self.root / "empty-scores.json"
        empty.write_text(json.dumps({"scores": {}}), encoding="utf-8")
        result = intake_actions.action_apply(
            str(self.report), str(empty), "t_intake1", self.config_path
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("no breakdown" in p for p in result["problems"]))
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM tasks")[0]["n"], 0)

    def test_verify_reports_complete_and_incomplete_graphs(self):
        applied = self._apply()
        advancing = {d["slug"]: d for d in applied["decisions"]}["build-a-widget-frobnicator"]
        ok = intake_actions.action_verify("build-a-widget-frobnicator", self.config_path)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["triage_task_id"], advancing["triage_task_id"])

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM tasks WHERE id = ?", (advancing["route_task_id"],))
        conn.commit()
        conn.close()
        broken = intake_actions.action_verify("build-a-widget-frobnicator", self.config_path)
        self.assertFalse(broken["ok"])
        self.assertIn("route card", broken["missing"])


if __name__ == "__main__":
    unittest.main()
