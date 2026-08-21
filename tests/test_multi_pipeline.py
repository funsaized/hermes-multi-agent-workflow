from pathlib import Path
import unittest

from engine.config import ConfigError, TriageConfig
from engine.skill_materialization import skill_targets
from tests.test_scaffold import ROOT, config_data


class MultiPipelineIsolationTests(unittest.TestCase):
    def config(self, pipeline_id: str, board: str, workspace: str) -> TriageConfig:
        data = config_data()
        data.update(pipeline_id=pipeline_id, board=board, workspace_root=workspace)
        data["hermes"]["profiles"]["researcher"]["shared"] = True
        return TriageConfig.from_dict(data, config_path=ROOT / f"{pipeline_id}.yaml")

    def test_shared_profiles_keep_board_workspace_and_resources_isolated(self):
        first = self.config("alpha", "alpha-board", "./work/alpha")
        second = self.config("beta", "beta-board", "./work/beta")

        self.assertEqual(first.role_to_profile("researcher"), second.role_to_profile("researcher"))
        self.assertNotEqual(first.board, second.board)
        self.assertNotEqual(first.workspace_path, second.workspace_path)
        self.assertNotEqual(first.orchestrator_skill, second.orchestrator_skill)
        self.assertNotEqual(first.cron_name(first.sources[0]), second.cron_name(second.sources[0]))
        self.assertTrue(set(target.skill for target in skill_targets(first)).isdisjoint(
            target.skill for target in skill_targets(second)
        ))

    def test_unique_profile_can_replace_one_shared_role(self):
        data = config_data()
        data["pipeline_id"] = "security"
        data["roles"]["researcher"] = "security-researcher"
        data["hermes"]["profiles"]["security-researcher"] = {
            "description": "Security-specific research worker.",
            "toolsets": ["web", "file"],
        }

        cfg = TriageConfig.from_dict(data, config_path=ROOT / "security.yaml")

        self.assertEqual(cfg.role_to_profile("researcher"), "security-researcher")
        self.assertFalse(cfg.hermes.profiles["security-researcher"].shared)

    def test_pipeline_id_rejects_unsafe_resource_names(self):
        data = config_data()
        data["pipeline_id"] = "Not Safe"
        with self.assertRaisesRegex(ConfigError, "pipeline_id"):
            TriageConfig.from_dict(data, config_path=Path(ROOT) / "unsafe.yaml")


if __name__ == "__main__":
    unittest.main()
