"""Read-only, structured checks for a configured Hermes deployment.

The preflight invokes only help, version, list, describe, and status surfaces.
It never opens Hermes configuration, environment, authentication, or database
files, and it never retains raw command output in its report.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import re
import subprocess
from typing import Protocol

from .config import TriageConfig, parse_version_triplet


ORCHESTRATOR_SKILL = "triage-orchestrator"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ToolsetInventory:
    """One execution surface's advertised and currently enabled toolsets."""

    inspected: bool
    available: frozenset[str]
    enabled: frozenset[str]


class CommandRunner(Protocol):
    """Narrow seam used by tests and by the subprocess-backed CLI."""

    def run(self, argv: tuple[str, ...]) -> CommandResult: ...


class SubprocessCommandRunner:
    """Run fixed argv without a shell; output is consumed but never reported."""

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        env = os.environ.copy()
        env.update({"NO_COLOR": "1", "COLUMNS": "240"})
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=env,
            )
        except FileNotFoundError:
            return CommandResult(127, "", "executable not found")
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", "command timed out")
        except OSError:
            return CommandResult(126, "", "executable not runnable")
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    severity: str
    evidence: str


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: tuple[PreflightCheck, ...]

    @classmethod
    def from_checks(
        cls,
        checks: tuple[PreflightCheck, ...],
        warnings: tuple[str, ...] = (),
    ) -> "PreflightReport":
        errors = tuple(check.evidence for check in checks if not check.ok and check.severity == "error")
        check_warnings = tuple(check.evidence for check in checks if not check.ok and check.severity == "warning")
        all_warnings = warnings + check_warnings
        return cls(not errors, errors, all_warnings, checks)

    def render_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "errors": list(self.errors),
                "warnings": list(self.warnings),
                "checks": [asdict(check) for check in self.checks],
            },
            indent=2,
            sort_keys=True,
        ) + "\n"

    def render_text(self) -> str:
        lines = ["Hermes preflight: " + ("OK" if self.ok else "BLOCKED")]
        for check in self.checks:
            marker = "OK" if check.ok else ("WARN" if check.severity == "warning" else "FAIL")
            lines.append(f"[{marker}] {check.name}: {check.evidence}")
        for warning in self.warnings:
            lines.append(f"[WARN] config: {warning}")
        return "\n".join(lines) + "\n"


def _output(result: CommandResult) -> str:
    return result.stdout + "\n" + result.stderr


def _has_token(text: str, token: str) -> bool:
    """Match an exact CLI/name token independent of columns or whitespace."""
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(token) + r"(?![A-Za-z0-9_-])", text) is not None


def _version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?i)\b(?:hermes(?:\s+agent)?\s+)?v?(\d+)\.(\d+)\.(\d+)\b", text)
    return tuple(map(int, match.groups())) if match else None  # type: ignore[return-value]


def _check(name: str, ok: bool, success: str, failure: str) -> PreflightCheck:
    return PreflightCheck(name, ok, "error", success if ok else failure)


def _enabled_toolsets(text: str) -> set[str]:
    enabled: set[str] = set()
    for line in text.splitlines():
        match = re.search(r"\benabled\s+([A-Za-z0-9_-]+)(?:\s|$)", line)
        if match:
            enabled.add(match.group(1))
    return enabled


def _available_toolsets(text: str) -> set[str]:
    """Return advertised names regardless of enabled state."""
    available: set[str] = set()
    for line in text.splitlines():
        match = re.search(r"\b(?:enabled|disabled)\s+([A-Za-z0-9_-]+)(?:\s|$)", line)
        if match:
            available.add(match.group(1))
    return available


def parse_toolset_inventory(result: CommandResult) -> ToolsetInventory:
    """Parse availability separately from enabled state for drift classification."""
    if result.returncode != 0:
        return ToolsetInventory(False, frozenset(), frozenset())
    output = _output(result)
    available = frozenset(_available_toolsets(output))
    return ToolsetInventory(
        bool(available),
        available,
        frozenset(_enabled_toolsets(output)),
    )


def run_preflight(cfg: TriageConfig, runner: CommandRunner | None = None) -> PreflightReport:
    """Inspect capabilities and deployment resources without changing them."""
    runner = runner or SubprocessCommandRunner()
    checks: list[PreflightCheck] = []

    version_result = runner.run(("hermes", "--version"))
    exists = version_result.returncode not in {126, 127}
    checks.append(
        _check(
            "runtime.executable",
            exists,
            "Hermes executable is available.",
            "Hermes executable was not found on PATH or is not runnable.",
        )
    )
    if not exists:
        return PreflightReport.from_checks(tuple(checks), cfg.validation_warnings)

    installed = _version(_output(version_result))
    minimum = parse_version_triplet(cfg.hermes.min_version)
    version_ok = version_result.returncode == 0 and installed is not None and minimum is not None and installed >= minimum
    installed_text = ".".join(map(str, installed)) if installed else "unparseable"
    checks.append(
        _check(
            "runtime.version",
            version_ok,
            f"Hermes {installed_text} satisfies minimum {cfg.hermes.min_version}.",
            f"Hermes version {installed_text} does not satisfy minimum {cfg.hermes.min_version}.",
        )
    )

    profile_flag = runner.run(("hermes", "-p", cfg.hermes.base_profile, "--version"))
    checks.append(
        _check(
            "capability.global-profile",
            profile_flag.returncode == 0,
            "Global -p/--profile selection is accepted.",
            "Hermes does not accept global -p/--profile selection for the configured base profile.",
        )
    )

    cron_surface = runner.run(
        ("hermes", "-p", cfg.hermes.base_profile, "tools", "list", "--platform", "cron")
    )
    cron_surface_ok = cron_surface.returncode == 0 and _has_token(_output(cron_surface), "cron")
    checks.append(
        _check(
            "capability.cron-tool-surface",
            cron_surface_ok,
            "The cron execution surface is available for toolset inspection.",
            "Hermes does not expose the required cron execution surface for toolsets.",
        )
    )

    toolset_surfaces = {
        "cli": (
            runner.run(
                ("hermes", "-p", cfg.hermes.base_profile, "tools", "list", "--platform", "cli")
            ),
            {
                toolset
                for profile in cfg.hermes.profiles.values()
                for toolset in profile.toolsets
            },
        ),
        "cron": (
            cron_surface,
            {
                toolset
                for profile in cfg.hermes.profiles.values()
                if profile.owns_cron
                for toolset in profile.toolsets
            },
        ),
    }
    for surface, (result, required) in toolset_surfaces.items():
        inventory = parse_toolset_inventory(result)
        missing = sorted(required - inventory.available)
        checks.append(
            _check(
                f"capability.toolset-names.{surface}",
                inventory.inspected and not missing,
                f"All configured {surface!r} toolset names are advertised by this Hermes runtime.",
                (
                    f"Configured toolset names are unavailable on the installed Hermes {surface!r} "
                    f"surface: {', '.join(missing)}."
                    if inventory.inspected
                    else f"Hermes toolset availability could not be inspected on the {surface!r} surface."
                ),
            )
        )

    capabilities = (
        ("profile-create", ("profile", "create"), ("--clone-from", "--description")),
        ("board-create", ("kanban", "boards", "create"), ("--default-workdir",)),
        ("tools-enable", ("tools", "enable"), ("--platform",)),
        ("cron-create", ("cron", "create"), ("--skill", "--workdir", "--deliver", "--name")),
        ("gateway-install", ("gateway", "install"), ("--start-now", "--start-on-login")),
    )
    for name, command, flags in capabilities:
        result = runner.run(("hermes", *command, "--help"))
        text = _output(result)
        missing = tuple(flag for flag in flags if not _has_token(text, flag))
        ok = result.returncode == 0 and not missing
        checks.append(
            _check(
                f"capability.{name}",
                ok,
                f"{' '.join(command)} supports {', '.join(flags)}.",
                f"{' '.join(command)} is unavailable or missing required flags: {', '.join(missing or flags)}.",
            )
        )

    board_result = runner.run(("hermes", "kanban", "boards", "list", "--json"))
    board_exists = False
    if board_result.returncode == 0:
        try:
            payload = json.loads(board_result.stdout)
            board_exists = isinstance(payload, list) and any(
                isinstance(item, dict) and item.get("slug") == cfg.board for item in payload
            )
        except (json.JSONDecodeError, TypeError):
            pass
    checks.append(_check("resource.board", board_exists, f"Board {cfg.board!r} exists.", f"Configured board {cfg.board!r} is missing or could not be listed as JSON."))

    profiles_result = runner.run(("hermes", "profile", "list"))
    profile_text = _output(profiles_result)
    for name, profile in cfg.hermes.profiles.items():
        exists = profiles_result.returncode == 0 and _has_token(profile_text, name)
        checks.append(_check(f"resource.profile.{name}", exists, f"Profile {name!r} exists.", f"Configured profile {name!r} is missing."))

        description_result = runner.run(("hermes", "profile", "describe", name))
        description_ok = description_result.returncode == 0 and profile.description.strip() == description_result.stdout.strip()
        checks.append(
            _check(
                f"resource.profile-description.{name}",
                description_ok,
                f"Profile {name!r} has the configured description.",
                f"Profile {name!r} is missing or its description differs from configuration.",
            )
        )

    required_skills: dict[str, set[str]] = {name: set() for name in cfg.hermes.profiles}
    required_skills[cfg.hermes.gateway_profile].add(ORCHESTRATOR_SKILL)
    for source in cfg.sources:
        required_skills[source.profile].add(source.skill)
    for profile, skills in required_skills.items():
        if not skills:
            continue
        result = runner.run(("hermes", "-p", profile, "skills", "list", "--enabled-only"))
        text = _output(result)
        for skill in sorted(skills):
            present = result.returncode == 0 and _has_token(text, skill)
            checks.append(_check(f"resource.skill.{profile}.{skill}", present, f"Skill {skill!r} is enabled for profile {profile!r}.", f"Required skill {skill!r} is missing or disabled for profile {profile!r}."))

    for profile, metadata in cfg.hermes.profiles.items():
        surfaces = ("cli", "cron") if metadata.owns_cron else ("cli",)
        for surface in surfaces:
            result = runner.run(("hermes", "-p", profile, "tools", "list", "--platform", surface))
            inventory = parse_toolset_inventory(result)
            missing = sorted(set(metadata.toolsets) - inventory.enabled)
            checks.append(
                _check(
                    f"resource.toolsets.{profile}.{surface}",
                    inventory.inspected and not missing,
                    f"Profile {profile!r} enables all configured toolsets on {surface!r}.",
                    f"Profile {profile!r} is missing toolsets on {surface!r}: {', '.join(missing or metadata.toolsets)}.",
                )
            )

    gateway_profiles = [cfg.hermes.gateway_profile]
    gateway_profiles.extend(
        name for name, profile in cfg.hermes.profiles.items()
        if profile.owns_cron and name != cfg.hermes.gateway_profile
    )
    for profile in gateway_profiles:
        result = runner.run(("hermes", "-p", profile, "gateway", "status"))
        status = _output(result).lower()
        active = result.returncode == 0 and (
            "gateway service is active" in status
            or ("service" in status and "loaded" in status and "not loaded" not in status)
        )
        checks.append(_check(f"resource.gateway.{profile}", active, f"Gateway service for profile {profile!r} is active.", f"Gateway service for profile {profile!r} is missing or inactive."))

    cron_status_profiles: set[str] = set()
    for source in cfg.sources:
        if source.profile not in cron_status_profiles:
            cron_status_profiles.add(source.profile)
            status_result = runner.run(("hermes", "-p", source.profile, "cron", "status"))
            status_text = _output(status_result).lower()
            cron_running = (
                status_result.returncode == 0
                and "running" in status_text
                and "not running" not in status_text
            )
            checks.append(
                _check(
                    f"resource.cron-status.{source.profile}",
                    cron_running,
                    f"Cron scheduler for profile {source.profile!r} is running.",
                    f"Cron scheduler for profile {source.profile!r} is missing or not running.",
                )
            )
        result = runner.run(("hermes", "-p", source.profile, "cron", "list", "--all"))
        job = f"{cfg.name}-{source.id}-scout"
        exists = result.returncode == 0 and _has_token(_output(result), job)
        checks.append(_check(f"resource.cron.{source.profile}.{job}", exists, f"Cron job {job!r} exists in profile {source.profile!r}.", f"Named cron job {job!r} is missing from profile {source.profile!r}."))

    gate_result = runner.run(("hermes", "-p", cfg.hermes.gateway_profile, "send", "--list", cfg.gate.channel))
    gate_text = _output(gate_result).lower()
    gate_ok = gate_result.returncode == 0 and "no targets found" not in gate_text
    checks.append(_check("resource.gate-channel", gate_ok, f"Gate channel {cfg.gate.channel!r} has an available delivery target.", f"Gate channel {cfg.gate.channel!r} has no available delivery target for profile {cfg.hermes.gateway_profile!r}."))

    return PreflightReport.from_checks(tuple(checks), cfg.validation_warnings)
