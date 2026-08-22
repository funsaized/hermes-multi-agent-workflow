from __future__ import annotations

import json
import io
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from cli.triage import main
from engine.config import TriageConfig
from engine.hermes_preflight import (
    CommandResult,
    PreflightReport,
    SubprocessCommandRunner,
    run_preflight,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    """Semantic Hermes fake: no developer installation or cosmetic layout."""

    def __init__(self, *, version: str = "0.20.1", deployed: bool = True):
        self.version = version
        self.deployed = deployed
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        self.calls.append(argv)
        if argv == ("hermes", "--version"):
            return CommandResult(0, f"Hermes Agent v{self.version}\n", "")
        if argv == ("hermes", "-p", "default", "--version"):
            return CommandResult(0, f"Hermes Agent v{self.version}\n", "")
        if argv[-1:] == ("--help",):
            flags = {
                ("hermes", "profile", "create", "--help"): "--clone-from --description",
                ("hermes", "kanban", "boards", "create", "--help"): "--default-workdir",
                ("hermes", "tools", "enable", "--help"): "--platform",
                ("hermes", "cron", "create", "--help"): "--skill --workdir --deliver --name",
                ("hermes", "gateway", "install", "--help"): "--start-now --start-on-login",
            }
            return CommandResult(0, flags.get(argv, ""), "")
        if argv == ("hermes", "kanban", "boards", "list", "--json"):
            boards = [{"slug": CONFIG.board}] if self.deployed else []
            return CommandResult(0, json.dumps(boards), "")
        if argv == ("hermes", "profile", "list"):
            names = " ".join(EXPECTED_PROFILES) if self.deployed else "default"
            return CommandResult(0, names, "")
        if len(argv) == 4 and argv[1:3] == ("profile", "describe"):
            name = argv[3]
            if not self.deployed:
                return CommandResult(1, "", f"Profile {name!r} does not exist")
            return CommandResult(0, CONFIG.hermes.profiles[name].description, "")
        if len(argv) >= 4 and argv[1] == "-p":
            profile = argv[2]
            if not self.deployed:
                return CommandResult(1, "", f"Profile {profile!r} does not exist")
            command = argv[3:]
            if command == ("skills", "list", "--enabled-only"):
                skills = []
                if profile == CONFIG.hermes.gateway_profile:
                    skills.append(CONFIG.orchestrator_skill)
                skills.extend(CONFIG.scout_skill(s) for s in CONFIG.sources if s.profile == profile)
                return CommandResult(0, "\n".join(skills), "")
            if command[:2] == ("tools", "list"):
                surface = command[-1]
                if profile == CONFIG.hermes.base_profile:
                    available = sorted({
                        toolset
                        for metadata in CONFIG.hermes.profiles.values()
                        for toolset in metadata.toolsets
                    })
                    enabled = (
                        set(CONFIG.hermes.profiles[profile].toolsets)
                        if profile in CONFIG.hermes.profiles
                        else set()
                    )
                    return CommandResult(
                        0,
                        f"Built-in toolsets ({surface}):\n"
                        + "\n".join(
                            f"{'enabled' if name in enabled else 'disabled'} {name}"
                            for name in available
                        ),
                        "",
                    )
                enabled = CONFIG.hermes.profiles[profile].toolsets
                return CommandResult(
                    0,
                    f"Built-in toolsets ({surface}):\n"
                    + "\n".join(f"enabled {name}" for name in enabled),
                    "",
                )
            if command == ("gateway", "status"):
                return CommandResult(0, "Gateway is supervised by launchd (PID 7748)", "")
            if command == ("cron", "list", "--all"):
                names = [
                    CONFIG.cron_name(source)
                    for source in CONFIG.sources
                    if source.profile == profile
                ]
                return CommandResult(0, "\n".join(names), "")
            if command == ("cron", "status"):
                return CommandResult(0, "Gateway is running; cron jobs will fire automatically", "")
            if command == ("send", "--list", CONFIG.gate.channel):
                return CommandResult(0, "discord:briefs [1484142557704491119]", "")
        return CommandResult(2, "", "unsupported fake command")


CONFIG = TriageConfig.load(ROOT / "triage-ai-engineering.yaml")
EXPECTED_PROFILES = tuple(CONFIG.hermes.profiles)


class PreflightTests(unittest.TestCase):
    def test_new_runtime_and_complete_resources_pass(self):
        runner = FakeRunner()
        report = run_preflight(CONFIG, runner)

        self.assertTrue(report.ok, report.render_text())
        self.assertEqual(report.errors, ())
        self.assertTrue(all(check.evidence for check in report.checks))
        self.assertIn(("hermes", "-p", "webresearch", "tools", "list", "--platform", "cron"), runner.calls)
        self.assertIn(("hermes", "-p", "default", "tools", "list", "--platform", "cron"), runner.calls)
        self.assertIn(("hermes", "-p", "webresearch", "cron", "list", "--all"), runner.calls)
        self.assertIn(("hermes", "-p", "webresearch", "cron", "status"), runner.calls)
        self.assertIn(("hermes", "-p", "default", "send", "--list", "discord"), runner.calls)
        self.assertNotIn(("hermes", "profile", "describe", "default"), runner.calls)
        self.assertTrue(all(call[-1] == "--help" for call in runner.calls if "create" in call or "install" in call))

    def test_old_runtime_and_missing_resources_are_blockers(self):
        report = run_preflight(CONFIG, FakeRunner(version="0.19.9", deployed=False))

        self.assertFalse(report.ok)
        self.assertTrue(any("0.19.9" in error for error in report.errors))
        self.assertTrue(any(CONFIG.board in error for error in report.errors))
        self.assertTrue(any(CONFIG.sources[0].skill in error for error in report.errors))
        self.assertTrue(any("discord:1484142557704491119" in error for error in report.errors))

    def test_missing_executable_short_circuits_safely(self):
        class MissingRunner:
            def run(self, argv: tuple[str, ...]) -> CommandResult:
                return CommandResult(127, "", "executable not found")

        report = run_preflight(CONFIG, MissingRunner())
        self.assertFalse(report.ok)
        self.assertEqual(len(report.checks), 1)
        self.assertNotIn("token", report.render_text().lower())

    def test_capability_flags_are_tokens_not_help_layout(self):
        class MissingFlagRunner(FakeRunner):
            def run(self, argv: tuple[str, ...]) -> CommandResult:
                result = super().run(argv)
                if argv == ("hermes", "cron", "create", "--help"):
                    return CommandResult(0, "usage reformatted arbitrarily: --skill --workdir --name", "")
                return result

        report = run_preflight(CONFIG, MissingFlagRunner())
        self.assertFalse(report.ok)
        self.assertTrue(any("--deliver" in error for error in report.errors))

    def test_missing_cron_execution_surface_is_a_capability_blocker(self):
        class NoCronSurfaceRunner(FakeRunner):
            def run(self, argv: tuple[str, ...]) -> CommandResult:
                if argv == ("hermes", "-p", "default", "tools", "list", "--platform", "cron"):
                    return CommandResult(2, "", "Unknown platform")
                return super().run(argv)

        report = run_preflight(CONFIG, NoCronSurfaceRunner())
        self.assertFalse(report.ok)
        self.assertTrue(any("cron execution surface" in error for error in report.errors))

    def test_unavailable_configured_toolset_names_are_capability_blockers(self):
        class MissingNameRunner(FakeRunner):
            def run(self, argv: tuple[str, ...]) -> CommandResult:
                result = super().run(argv)
                if (
                    len(argv) >= 7
                    and argv[:4] == ("hermes", "-p", "default", "tools")
                    and argv[4:] in {
                        ("list", "--platform", "cli"),
                        ("list", "--platform", "cron"),
                    }
                ):
                    lines = [line for line in result.stdout.splitlines() if not line.endswith(" terminal")]
                    return CommandResult(0, "\n".join(lines), "")
                return result

        report = run_preflight(CONFIG, MissingNameRunner())

        self.assertFalse(report.ok)
        failed = {check.name: check.evidence for check in report.checks if not check.ok}
        self.assertIn("capability.toolset-names.cli", failed)
        self.assertIn("capability.toolset-names.cron", failed)
        self.assertIn("terminal", failed["capability.toolset-names.cli"])

    def test_windows_gateway_process_status_is_active(self):
        class WindowsGatewayRunner(FakeRunner):
            def run(self, argv: tuple[str, ...]) -> CommandResult:
                if argv == ("hermes", "-p", "default", "gateway", "status"):
                    return CommandResult(
                        0,
                        "Scheduled Task registered: Hermes_Gateway\n"
                        "Status: Ready\n"
                        "Gateway process running (PID: 16068)\n",
                        "",
                    )
                return super().run(argv)

        report = run_preflight(CONFIG, WindowsGatewayRunner())
        gateway = next(check for check in report.checks if check.name == "resource.gateway.default")
        self.assertTrue(gateway.ok, gateway.evidence)

    @patch("engine.hermes_preflight.subprocess.run", side_effect=PermissionError("denied"))
    def test_permission_error_is_returned_as_a_structured_runner_failure(self, _run):
        result = SubprocessCommandRunner().run(("hermes", "--version"))
        self.assertEqual(result, CommandResult(126, "", "executable not runnable"))

    def test_runner_survives_non_utf8_output(self):
        result = SubprocessCommandRunner().run(
            (sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([0x8d]))")
        )
        self.assertEqual(result, CommandResult(0, "\ufffd", ""))

    def test_description_mismatch_and_disabled_toolset_are_reported(self):
        class DriftRunner(FakeRunner):
            def run(self, argv: tuple[str, ...]) -> CommandResult:
                result = super().run(argv)
                if argv == ("hermes", "profile", "describe", "builder"):
                    return CommandResult(0, "old description", "")
                if argv == ("hermes", "-p", "webresearch", "tools", "list", "--platform", "cron"):
                    return CommandResult(0, "enabled file\nenabled kanban\ndisabled web", "")
                return result

        report = run_preflight(CONFIG, DriftRunner())
        self.assertTrue(any("builder" in error and "description" in error for error in report.errors))
        self.assertTrue(any("web" in error and "cron" in error for error in report.errors))

    def test_json_and_text_rendering_are_structured_and_redacted(self):
        report = run_preflight(CONFIG, FakeRunner(deployed=False))
        payload = json.loads(report.render_json())

        self.assertEqual(payload["ok"], report.ok)
        self.assertEqual(payload["errors"], list(report.errors))
        self.assertTrue(all(set(item) == {"name", "ok", "severity", "evidence"} for item in payload["checks"]))
        rendered = report.render_text() + report.render_json()
        self.assertNotIn("auth.json", rendered)
        self.assertNotIn(".env", rendered)
        self.assertNotIn("bot", rendered.lower())

    def test_report_requires_no_secret_bearing_raw_output(self):
        report = PreflightReport.from_checks(())
        self.assertTrue(report.ok)
        self.assertEqual(json.loads(report.render_json())["checks"], [])

    def test_cli_json_returns_nonzero_for_blockers(self):
        report = run_preflight(CONFIG, FakeRunner(deployed=False))
        stdout = io.StringIO()
        with patch("cli.triage.run_preflight", return_value=report), redirect_stdout(stdout):
            code = main(["--config", str(ROOT / "triage-ai-engineering.yaml"), "preflight", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])

    def test_scaffold_warns_from_preflight_but_still_renders_plan(self):
        report = run_preflight(CONFIG, FakeRunner(deployed=False))
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("cli.triage.run_preflight", return_value=report),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(["--config", str(ROOT / "triage-ai-engineering.yaml"), "scaffold", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(stdout.getvalue()), list)
        self.assertIn("preflight warning", stderr.getvalue().lower())

    def test_scaffold_no_preflight_skips_live_runtime_inspection(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("cli.triage.run_preflight") as preflight,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main([
                "--config", str(ROOT / "triage-ai-engineering.yaml"),
                "scaffold", "--format", "json", "--no-preflight",
            ])
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(stdout.getvalue()), list)
        self.assertEqual(stderr.getvalue(), "")
        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
