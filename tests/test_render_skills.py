from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from engine.config import TriageConfig
from engine.skill_materialization import (
    SkillTemplateError,
    materialize_skills,
    skill_targets,
)
from engine.scaffold import ManualCheckpoint, build_deployment_plan
from tests.test_scaffold import ROOT, config_data, write_config


class SkillMaterializationTests(unittest.TestCase):
    def test_renders_source_scout_and_orchestrator_to_profile_local_staging(self):
        with tempfile.TemporaryDirectory(prefix="skill render ") as tmp:
            project = Path(tmp)
            cfg = TriageConfig.load(write_config(project, config_data()))
            rendered = materialize_skills(
                cfg,
                template_root=ROOT / "skills" / "templates",
            )

            self.assertEqual([item.skill for item in rendered], ["test-pipeline-test-scout", "triage-test-pipeline"])
            scout, orchestrator = rendered
            self.assertEqual(
                scout.path,
                project.resolve() / "work/scaffold/profiles/scout/skills/test-pipeline-test-scout/SKILL.md",
            )
            self.assertEqual(
                orchestrator.path,
                project.resolve() / "work/scaffold/profiles/orchestrator/skills/triage-test-pipeline/SKILL.md",
            )
            text = scout.path.read_text(encoding="utf-8")
            self.assertIn("name: test-pipeline-test-scout", text)
            self.assertIn("# Triage scout: web", text)
            self.assertIn("Find concrete reports with sources.", text)
            self.assertIn('--assignee "orchestrator"', text)
            self.assertIn('hermes kanban --board "test-board" create', text)
            self.assertIn('--skill triage-test-pipeline', text)
            self.assertIn('test-pipeline:intake:web:', text)
            self.assertIn(str(project.resolve() / "work/vault/intake"), text)
            self.assertIn(str(project.resolve()), text)
            self.assertNotIn("TODO", text)
            self.assertNotIn("{{", text)

            frontmatter = yaml.safe_load(text.split("---", 2)[1])
            self.assertEqual(frontmatter["name"], "test-pipeline-test-scout")
            orchestrator_text = orchestrator.path.read_text(encoding="utf-8")
            self.assertIn("test-board", orchestrator_text)
            self.assertIn(str(project.resolve()), orchestrator_text)
            self.assertIn("hermes send --to discord:briefs", orchestrator_text)
            self.assertIn("python pre_gate_actions.py --config", orchestrator_text)
            self.assertIn("approve     test-pipeline:<slug>", orchestrator_text)
            self.assertIn("Never create prep cards yourself", orchestrator_text)
            self.assertIn("test-pipeline:triage:<intake-id>:<slug>", orchestrator_text)
            self.assertIn("parented to the current intake task", orchestrator_text)
            self.assertIn("linked_kanban_tasks` frontmatter", orchestrator_text)
            self.assertIn("A worker may\nonly complete its own task", orchestrator_text)
            self.assertIn("must never\ncreate research cards", orchestrator_text)
            self.assertIn("Archived cards are historical evidence", orchestrator_text)
            self.assertIn("Do not inspect the\nKanban SQLite database directly", orchestrator_text)
            self.assertIn("Never\nshell to `hermes kanban`", orchestrator_text)
            self.assertIn("as the `parents` array", orchestrator_text)
            self.assertIn("classifier_spec(slug, evidence_ids)", orchestrator_text)
            self.assertIn("parented only to the classifier", orchestrator_text)
            self.assertIn("persistent project workspace", orchestrator_text)
            self.assertNotIn("TODO", orchestrator_text)
            self.assertNotIn("{{", orchestrator_text)

    def test_output_is_deterministic_and_reports_exact_manual_destinations(self):
        with tempfile.TemporaryDirectory(prefix="skill render ") as tmp:
            cfg = TriageConfig.load(write_config(Path(tmp), config_data()))
            kwargs = {"template_root": ROOT / "skills" / "templates"}
            first = materialize_skills(cfg, **kwargs)
            snapshots = [item.path.read_bytes() for item in first]
            second = materialize_skills(cfg, **kwargs)
            self.assertEqual(snapshots, [item.path.read_bytes() for item in second])
            targets = skill_targets(cfg)
            self.assertEqual(targets[0].live_destination, "$HERMES_HOME/profiles/scout/skills/test-pipeline-test-scout/SKILL.md")
            self.assertTrue(targets[0].path.is_absolute())
            skill_step = next(
                step for step in build_deployment_plan(cfg).steps
                if isinstance(step, ManualCheckpoint) and step.phase == "skills"
            )
            instructions = "\n".join(skill_step.verification)
            self.assertIn(str(targets[0].path), instructions)
            self.assertIn(targets[0].live_destination, instructions)
            self.assertIn("Automatic `hermes profile install` remains deferred", instructions)

    def test_base_profile_skill_destination_uses_root_hermes_home(self):
        data = config_data()
        data["roles"]["orchestrator"] = "default"
        data["hermes"]["gateway_profile"] = "default"
        data["hermes"]["profiles"]["default"] = data["hermes"]["profiles"].pop("orchestrator")
        cfg = TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")

        orchestrator = skill_targets(cfg)[-1]

        self.assertEqual(
            orchestrator.live_destination,
            "$HERMES_HOME/skills/triage-test-pipeline/SKILL.md",
        )

    def test_rejects_unresolved_placeholders_and_invalid_frontmatter(self):
        with tempfile.TemporaryDirectory(prefix="bad templates ") as tmp:
            root = Path(tmp)
            (root / "triage-scout").mkdir()
            (root / "triage-orchestrator").mkdir()
            (root / "triage-scout/SKILL.md").write_text("---\nname: {{SKILL_NAME}}\n---\nTODO: fill me\n")
            (root / "triage-orchestrator/SKILL.md").write_text("not frontmatter\n")
            cfg = TriageConfig.from_dict(config_data(), config_path=ROOT / "triage.yaml")
            with self.assertRaises(SkillTemplateError):
                materialize_skills(cfg, output_root=root / "out", template_root=root)

    def test_legitimate_todo_query_content_is_not_a_placeholder(self):
        with tempfile.TemporaryDirectory(prefix="skill render ") as tmp:
            data = config_data()
            data["sources"][0]["query"] = "Find reports about TODO-app agents."
            cfg = TriageConfig.load(write_config(Path(tmp), data))

            rendered = materialize_skills(
                cfg,
                template_root=ROOT / "skills" / "templates",
            )

            self.assertIn(
                "Find reports about TODO-app agents.",
                rendered[0].path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
