"""Checks for the graph-engineering pipeline configuration."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import TriageConfig  # noqa: E402
from engine.engine import TriageEngine  # noqa: E402


class TestGraphEngineeringDomain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = TriageConfig.load(ROOT / "triage-graph-eng.yaml")

    def test_pipeline_is_isolated_and_reuses_specialists(self):
        current = TriageConfig.load(ROOT / "triage.yaml")
        self.assertNotEqual(self.cfg.pipeline_id, current.pipeline_id)
        self.assertNotEqual(self.cfg.board, current.board)
        self.assertNotEqual(self.cfg.workspace_root, current.workspace_root)
        self.assertFalse(self.cfg.hermes.profiles["graph-research"].shared)
        self.assertTrue(self.cfg.hermes.profiles["researcher"].shared)

    def test_x_scout_enforces_discover_verify_then_optional_xurl(self):
        source = next(s for s in self.cfg.sources if s.id == "x_research")
        profile = self.cfg.hermes.profiles[source.profile]
        query = source.query.lower()
        self.assertTrue({"x_search", "terminal"}.issubset(profile.toolsets))
        self.assertTrue(profile.owns_cron)
        self.assertLess(query.index("discover"), query.index("verify"))
        self.assertLess(query.index("verify"), query.index("switch to xurl"))
        self.assertIn("get-only", query)
        self.assertIn("never start an auth flow", query)
        self.assertIn("if xurl is unavailable", query)

    def test_web_research_scout_has_an_independent_source_contract(self):
        source = next(s for s in self.cfg.sources if s.id == "web_research")
        profile = self.cfg.hermes.profiles[source.profile]
        query = source.query.lower()
        self.assertIn("web", profile.toolsets)
        self.assertIn("follow promising claims back", query)
        self.assertIn("independent source", query)

    def test_routes_and_artifacts_are_complete(self):
        self.assertEqual(
            set(self.cfg.route.map.values()),
            {"tutorial", "worked_example", "hands_on_lab", "enterprise_reference", "shelve"},
        )
        for name, path in self.cfg.paths.items():
            if path.auto:
                continue
            for rel in (path.proposal_template, path.scope_rails, path.deliverable_spec):
                self.assertIsNotNone(rel, name)
                self.assertTrue((ROOT / rel).is_file(), rel)

    def test_research_and_fulfillment_use_persistent_workspaces(self):
        engine = TriageEngine(self.cfg)
        self.assertTrue(all(s.workspace_kind == "dir" for s in engine.research_specs("sample", "root")))
        for name, path in self.cfg.paths.items():
            if path.auto:
                continue
            specs = engine.fulfillment_specs("sample", name)
            self.assertTrue(all(s.workspace_kind == "dir" for s in specs), name)
            self.assertEqual(1, len({s.workspace_path for s in specs}), name)


if __name__ == "__main__":
    unittest.main()
