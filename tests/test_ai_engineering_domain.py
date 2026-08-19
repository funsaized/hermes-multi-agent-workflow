"""Checks for the shipped AI Engineering Skills Map domain configuration."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import TriageConfig  # noqa: E402
from engine.engine import TriageEngine  # noqa: E402


class TestAIEngineeringDomain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = TriageConfig.load(ROOT / "triage.yaml")

    def test_all_learning_formats_route(self):
        expected = {
            "focused_knowledge_gap": "research_brief",
            "conceptual_model_gap": "explainer",
            "applied_skill_gap": "hands_on_lab",
            "broad_dependency_gap": "full_course",
            "learner_variability_high": "adaptive_curriculum",
            "already_covered": "shelve",
            "insufficient_evidence": "shelve",
        }
        self.assertEqual(self.cfg.route.map, expected)

    def test_every_artifact_path_has_templates(self):
        for name, path in self.cfg.paths.items():
            if path.auto:
                continue
            for rel in (path.proposal_template, path.scope_rails, path.deliverable_spec):
                self.assertIsNotNone(rel, name)
                self.assertTrue((ROOT / rel).is_file(), rel)

    def test_research_workers_receive_lane_contract(self):
        engine = TriageEngine(self.cfg)
        bodies = [spec.body for spec in engine.research_specs("rag-evals", "root")]
        self.assertTrue(all("# Research lane guide" in body for body in bodies))
        self.assertTrue(any("recommended_format" in body for body in bodies))

    def test_fulfillment_paths_are_persistent(self):
        engine = TriageEngine(self.cfg)
        for name, path in self.cfg.paths.items():
            if path.auto:
                continue
            specs = engine.fulfillment_specs("sample", name)
            self.assertTrue(specs, name)
            self.assertTrue(all(spec.workspace_kind == "dir" for spec in specs), name)
            self.assertEqual(len({spec.workspace_path for spec in specs}), 1, name)


if __name__ == "__main__":
    unittest.main()
