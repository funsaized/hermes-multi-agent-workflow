from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from engine.config import ConfigError, TriageConfig
from engine.scaffold import (
    CommandStep,
    ManualCheckpoint,
    PHASES,
    build_deployment_plan,
    render_json,
    render_shell,
)


ROOT = Path(__file__).resolve().parent.parent


def config_data() -> dict:
    return {
        "name": "test pipeline",
        "board": "test-board",
        "workspace_root": "./work",
        "sources": [
            {
                "id": "web",
                "profile": "scout",
                "skill": "test-scout",
                "schedule": "15 * * * *",
                "query": "Find concrete reports with sources.",
            }
        ],
        "item_schema": {"fields": ["title", "claim", "sources"]},
        "rubric": {"threshold": 1, "dimensions": [{"key": "value", "max": 1}]},
        "research_lanes": {"role": "researcher", "lanes": ["audit"], "classifier_lane": "audit"},
        "route": {"classifier": "audit.result", "map": {"good": "shelve"}},
        "paths": {"shelve": {"auto": True}},
        "roles": {"orchestrator": "orchestrator", "researcher": "researcher"},
        "gate": {"channel": "discord", "target": "discord:briefs"},
        "hermes": {
            "min_version": "0.20.0",
            "base_profile": "default",
            "gateway_profile": "orchestrator",
            "project_root": ".",
            "profile_strategy": "clone",
            "max_spawn": 3,
            "profiles": {
                "orchestrator": {
                    "description": "Routes work and handles the human gate.",
                    "toolsets": ["file", "terminal", "kanban"],
                },
                "researcher": {
                    "description": "Performs research.",
                    "toolsets": ["web", "file"],
                },
                "scout": {
                    "description": "Scheduled web scout.",
                    "toolsets": ["web", "file", "kanban"],
                    "owns_cron": True,
                },
            },
        },
    }


def write_config(directory: Path, data: dict | None = None) -> Path:
    path = directory / "triage.yaml"
    path.write_text(yaml.safe_dump(data or config_data(), sort_keys=False), encoding="utf-8")
    return path


class DeploymentConfigTests(unittest.TestCase):
    def test_parses_typed_deployment_and_resolves_root_from_config(self):
        with tempfile.TemporaryDirectory(prefix="hermes config ") as tmp:
            config_path = write_config(Path(tmp))
            cfg = TriageConfig.load(config_path)
        self.assertEqual(cfg.hermes.min_version, "0.20.0")
        self.assertEqual(cfg.hermes.base_profile, "default")
        self.assertEqual(cfg.hermes.gateway_profile, "orchestrator")
        self.assertEqual(cfg.hermes.project_root, str(config_path.parent.resolve()))
        self.assertEqual(cfg.hermes.profiles["scout"].toolsets, ("web", "file", "kanban"))
        self.assertTrue(cfg.hermes.profiles["scout"].owns_cron)

    def test_missing_hermes_is_backward_compatible_and_warns(self):
        data = config_data()
        del data["hermes"]
        cfg = TriageConfig.from_dict(data)
        self.assertEqual(set(cfg.hermes.profiles), {"orchestrator", "researcher", "scout"})
        self.assertTrue(cfg.hermes.profiles["scout"].owns_cron)
        self.assertTrue(any("descriptions and toolsets are unspecified" in warning for warning in cfg.validation_warnings))

    def test_legacy_fallback_prefers_a_role_mapped_gateway_and_warns(self):
        data = config_data()
        del data["hermes"]
        data["roles"] = {"researcher": "z-worker"}
        data["paths"]["shelve"]["propose"] = {"role": "researcher"}

        cfg = TriageConfig.from_dict(data)

        self.assertEqual(cfg.hermes.gateway_profile, "z-worker")
        self.assertIn("z-worker", cfg.roles.values())
        self.assertTrue(cfg.validation_warnings)

    def test_accepts_prerelease_and_build_min_versions(self):
        for version in ("0.20.0-rc1", "0.20.0+build.7", "0.20.0-rc1+build.7"):
            with self.subTest(version=version):
                data = config_data()
                data["hermes"]["min_version"] = version
                cfg = TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")
                self.assertEqual(cfg.hermes.min_version, version)

    def test_rejects_duplicate_source_ids_and_profile_skill_pairs(self):
        data = config_data()
        duplicate_id = dict(data["sources"][0], profile="scout", skill="another-scout")
        data["sources"].append(duplicate_id)
        with self.assertRaisesRegex(ConfigError, r"Duplicate sources\[\]\.id 'web'"):
            TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")

        data = config_data()
        duplicate_target = dict(data["sources"][0], id="another-web")
        data["sources"].append(duplicate_target)
        with self.assertRaisesRegex(ConfigError, r"Duplicate sources\[\] \(profile, skill\) pair"):
            TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")

    def test_rejects_invalid_deployment_relationships(self):
        mutations = {
            "gateway missing": lambda d: d["hermes"].update(gateway_profile="ghost"),
            "source missing": lambda d: d["hermes"]["profiles"].pop("scout"),
            "cron without source": lambda d: d["hermes"]["profiles"]["researcher"].update(owns_cron=True),
            "empty description": lambda d: d["hermes"]["profiles"]["scout"].update(description=" "),
            "unsupported strategy": lambda d: d["hermes"].update(profile_strategy="distribution"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                data = config_data()
                mutate(data)
                with self.assertRaises(ConfigError):
                    TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")

    def test_relative_project_root_requires_config_location(self):
        with self.assertRaisesRegex(ConfigError, "relative.*config file location"):
            TriageConfig.from_dict(config_data())

    def test_gate_target_must_match_gate_channel(self):
        data = config_data()
        data["gate"]["target"] = "telegram:briefs"

        with self.assertRaisesRegex(ConfigError, "gate.target.*gate.channel"):
            TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")


class DeploymentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = TriageConfig.from_dict(config_data(), config_path=ROOT / "triage.yaml")
        self.plan = build_deployment_plan(self.cfg)

    def test_exact_phase_order(self):
        seen = []
        for step in self.plan.steps:
            if step.phase not in seen:
                seen.append(step.phase)
        self.assertEqual(tuple(seen), PHASES)

    def test_exact_modern_command_argv(self):
        commands = [step.argv for step in self.plan.steps if isinstance(step, CommandStep)]
        root = str(ROOT.resolve())
        self.assertIn(
            ("hermes", "kanban", "boards", "create", "test-board", "--name", "test pipeline", "--description", "Dedicated board for the test pipeline pipeline.", "--default-workdir", root),
            commands,
        )
        create_index = commands.index(
            ("hermes", "kanban", "boards", "create", "test-board", "--name", "test pipeline", "--description", "Dedicated board for the test pipeline pipeline.", "--default-workdir", root)
        )
        self.assertEqual(commands[create_index + 1], ("hermes", "kanban", "boards", "switch", "test-board"))
        self.assertIn(
            ("hermes", "profile", "create", "scout", "--clone-from", "default", "--no-alias", "--description", "Scheduled web scout."),
            commands,
        )
        self.assertIn(("hermes", "-p", "scout", "config", "set", "terminal.cwd", root), commands)
        self.assertIn(("hermes", "-p", "scout", "tools", "enable", "web", "file", "kanban", "--platform", "cli"), commands)
        self.assertIn(("hermes", "-p", "scout", "tools", "enable", "web", "file", "kanban", "--platform", "cron"), commands)
        self.assertIn(("hermes", "-p", "orchestrator", "config", "set", "kanban.dispatch_in_gateway", "true"), commands)
        self.assertIn(("hermes", "-p", "orchestrator", "config", "set", "kanban.max_spawn", "3"), commands)
        self.assertIn(("hermes", "-p", "scout", "config", "set", "kanban.dispatch_in_gateway", "false"), commands)
        cron = next(command for command in commands if command[:5] == ("hermes", "-p", "scout", "cron", "create"))
        self.assertEqual(cron[5], "15 * * * *")
        self.assertIn("Find concrete reports with sources.", cron[6])
        self.assertEqual(cron[7:], ("--name", "test pipeline-web-scout", "--skill", "test-scout", "--workdir", root, "--deliver", "local"))
        self.assertIn(("hermes", "-p", "orchestrator", "gateway", "install", "--start-now", "--start-on-login"), commands)
        self.assertIn(("hermes", "-p", "scout", "gateway", "install", "--start-now", "--start-on-login"), commands)

    def test_topology_cron_toolsets_and_no_credentials(self):
        commands = [step for step in self.plan.steps if isinstance(step, CommandStep)]
        gateways = [step.profile for step in commands if step.argv[3:5] == ("gateway", "install")]
        self.assertEqual(gateways, ["orchestrator", "scout"])
        cron_toolsets = [
            step for step in commands
            if step.argv[-2:] == ("--platform", "cron")
        ]
        self.assertEqual([step.profile for step in cron_toolsets], ["scout"])
        scout_cli = next(
            index for index, step in enumerate(commands)
            if step.argv[-2:] == ("--platform", "cli") and step.profile == "scout"
        )
        self.assertEqual(commands[scout_cli + 1], cron_toolsets[0])
        flat = "\n".join(" ".join(step.argv) for step in commands)
        self.assertNotIn("--profile", flat)
        self.assertNotIn("--from", flat)
        self.assertNotIn("api_key", flat.lower())
        discovery = [step for step in self.plan.steps if isinstance(step, ManualCheckpoint) and step.phase == "runtime preflight"]
        self.assertTrue(any("cron" in " ".join(step.verification).lower() and "platform" in " ".join(step.verification).lower() for step in discovery))

    def test_existing_base_profile_is_reused_as_gateway(self):
        data = config_data()
        data["roles"]["orchestrator"] = "default"
        data["hermes"]["gateway_profile"] = "default"
        data["hermes"]["profiles"]["default"] = data["hermes"]["profiles"].pop("orchestrator")
        cfg = TriageConfig.from_dict(data, config_path=ROOT / "triage.yaml")

        commands = [step.argv for step in build_deployment_plan(cfg).steps if isinstance(step, CommandStep)]

        self.assertFalse(any(command[:4] == ("hermes", "profile", "create", "default") for command in commands))
        self.assertNotIn(("hermes", "-p", "default", "gateway", "install", "--start-now", "--start-on-login"), commands)
        self.assertNotIn(("hermes", "-p", "default", "config", "set", "terminal.cwd", str(ROOT.resolve())), commands)
        self.assertIn(("hermes", "-p", "default", "config", "set", "kanban.dispatch_in_gateway", "true"), commands)

    @patch("subprocess.run")
    def test_planner_never_runs_subprocess(self, run):
        build_deployment_plan(self.cfg)
        run.assert_not_called()


class RendererTests(unittest.TestCase):
    def test_shell_uses_safe_quoting_and_checkpoints_are_comments(self):
        with tempfile.TemporaryDirectory(prefix="project with spaces ") as tmp:
            path = write_config(Path(tmp))
            shell = render_shell(build_deployment_plan(TriageConfig.load(path)))
        self.assertIn("'15 * * * *'", shell)
        self.assertIn("'Scheduled web scout.'", shell)
        self.assertIn("project with spaces ", shell)
        self.assertIn("--default-workdir '", shell)
        self.assertNotIn("<placeholder>", shell)
        for line in shell.splitlines():
            if "CHECKPOINT" in line:
                self.assertTrue(line.startswith("#"))

    def test_json_has_all_structured_fields(self):
        cfg = TriageConfig.from_dict(config_data(), config_path=ROOT / "triage.yaml")
        payload = json.loads(render_json(build_deployment_plan(cfg)))
        self.assertEqual(payload[0]["phase"], "runtime preflight")
        for step in payload:
            self.assertEqual(
                set(step),
                {"phase", "description", "argv", "profile", "mutates", "requires_human", "verification"},
            )


if __name__ == "__main__":
    unittest.main()
