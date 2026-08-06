#!/usr/bin/env python3
"""Safely rehearse scaffold state in an automatically deleted Hermes home.

No gateway, provider, authentication, or messaging command is executed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from engine.config import TriageConfig
from engine.hermes_preflight import (
    CommandResult,
    PreflightCheck,
    ToolsetInventory,
    parse_toolset_inventory,
    run_preflight,
)
from engine.scaffold import CommandStep, build_deployment_plan
from engine.skill_materialization import materialize_skills


@dataclass(frozen=True)
class RehearsalReport:
    ok: bool
    evidence: tuple[str, ...]
    runtime_compatibility_blocked: bool = False


class IsolatedRunner:
    def __init__(self, env: dict[str, str]):
        self.env = env

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        result = subprocess.run(argv, text=True, capture_output=True, env=self.env, check=False, timeout=30)
        return CommandResult(result.returncode, result.stdout, result.stderr)


def _run(argv: tuple[str, ...], env: dict[str, str]) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, env=env, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(argv)}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout + result.stderr


def _isolated_env(home: Path) -> dict[str, str]:
    # Deliberately do not inherit provider keys, HERMES_* overrides, or the real
    # HOME. PATH is the only host-specific input needed to locate the executable.
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home / "os-home"),
        "HERMES_HOME": str(home / "hermes"),
        "NO_COLOR": "1",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TMPDIR": str(home / "tmp"),
    }


def _prove_home_isolation(home: Path, env: dict[str, str]) -> None:
    """Use a filesystem-only sentinel before permitting any Hermes mutation."""
    sentinel = home / "hermes" / "profiles" / "hermesisolationprobe"
    sentinel.mkdir(parents=True)
    listing = _run(("hermes", "profile", "list"), env)
    if "hermesisolationprobe" not in listing:
        raise RuntimeError(
            "Hermes did not discover a sentinel under temporary HERMES_HOME; "
            "refusing all mutating rehearsal commands."
        )
    sentinel.rmdir()


def _toolset_inventories(
    cfg: TriageConfig,
    runner: IsolatedRunner,
) -> dict[tuple[str, str], ToolsetInventory]:
    targets = {
        (cfg.hermes.base_profile, "cli"),
        (cfg.hermes.base_profile, "cron"),
    }
    for profile, metadata in cfg.hermes.profiles.items():
        targets.add((profile, "cli"))
        if metadata.owns_cron:
            targets.add((profile, "cron"))
    return {
        target: parse_toolset_inventory(
            runner.run(("hermes", "-p", target[0], "tools", "list", "--platform", target[1]))
        )
        for target in sorted(targets)
    }


def classify_runtime_toolset_failures(
    cfg: TriageConfig,
    failures: list[PreflightCheck],
    inventories: dict[tuple[str, str], ToolsetInventory],
) -> tuple[list[PreflightCheck], list[PreflightCheck]]:
    """Separate genuine runtime name drift from missing-but-available product failures."""
    skippable: list[PreflightCheck] = []
    product_failures: list[PreflightCheck] = []
    for failure in failures:
        if failure.name.startswith("capability.toolset-names."):
            surface = failure.name.rsplit(".", 1)[-1]
            inventory = inventories.get((cfg.hermes.base_profile, surface))
            required = {
                toolset
                for metadata in cfg.hermes.profiles.values()
                if surface == "cli" or metadata.owns_cron
                for toolset in metadata.toolsets
            }
            if inventory and inventory.inspected and required - inventory.available:
                skippable.append(failure)
            else:
                product_failures.append(failure)
            continue

        prefix = "resource.toolsets."
        if not failure.name.startswith(prefix):
            product_failures.append(failure)
            continue
        profile, separator, surface = failure.name[len(prefix):].rpartition(".")
        inventory = inventories.get((profile, surface)) if separator else None
        metadata = cfg.hermes.profiles.get(profile)
        if not inventory or not inventory.inspected or metadata is None:
            product_failures.append(failure)
            continue
        missing_enabled = set(metadata.toolsets) - inventory.enabled
        missing_available = set(metadata.toolsets) - inventory.available
        if missing_enabled and missing_enabled == missing_available:
            skippable.append(failure)
        else:
            product_failures.append(failure)
    return skippable, product_failures


def run_rehearsal(config: str | Path = "triage.yaml") -> RehearsalReport:
    if shutil.which("hermes") is None:
        return RehearsalReport(False, ("Hermes executable is unavailable.",))
    cfg = TriageConfig.load(config)
    evidence: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hermes-scaffold-rehearsal-") as tmp:
        home = Path(tmp)
        (home / "os-home").mkdir()
        (home / "tmp").mkdir()
        env = _isolated_env(home)
        _prove_home_isolation(home, env)
        evidence.append(f"Hermes honored disposable HERMES_HOME {home / 'hermes'} before mutation.")

        plan = build_deployment_plan(cfg)
        for step in plan.steps:
            if not isinstance(step, CommandStep):
                continue
            if step.phase in {"board", "profiles", "profile working directories", "toolsets"}:
                _run(step.argv, env)
            elif step.phase == "cron" and step.argv[3:5] == ("config", "set"):
                _run(step.argv, env)

        rendered = materialize_skills(
            cfg,
            output_root=home / "staging" / "profiles",
            template_root=Path(cfg.hermes.project_root) / "skills" / "templates",
        )
        for target in rendered:
            destination = home / "hermes" / "profiles" / target.profile / "skills" / target.skill / "SKILL.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target.path, destination)
            if not destination.is_file():
                raise RuntimeError(f"Rendered skill was not installed in disposable profile: {destination}")

        for step in plan.steps:
            if not isinstance(step, CommandStep) or step.phase != "cron" or step.argv[3:5] != ("cron", "create"):
                continue
            argv = list(step.argv)
            argv[5] = "0 0 1 1 *"  # local-only, next-occurrence annual schedule; no gateway is started
            _run(tuple(argv), env)

        profile_listing = _run(("hermes", "profile", "list"), env)
        for profile in cfg.hermes.profiles:
            if profile not in profile_listing:
                raise RuntimeError(f"Disposable profile {profile!r} was not listed.")
            cwd = _run(("hermes", "-p", profile, "config", "get", "terminal.cwd", "--json"), env)
            if cfg.hermes.project_root not in cwd:
                raise RuntimeError(f"Profile {profile!r} has the wrong terminal.cwd: {cwd.strip()}")

        dispatcher_expectations = {cfg.hermes.gateway_profile: True}
        dispatcher_expectations.update({source.profile: False for source in cfg.sources})
        for profile, expected in dispatcher_expectations.items():
            raw = _run(("hermes", "-p", profile, "config", "get", "kanban.dispatch_in_gateway", "--json"), env)
            try:
                actual = json.loads(raw.strip())
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Profile {profile!r} returned invalid dispatcher JSON: {raw.strip()}") from exc
            if actual is not expected:
                raise RuntimeError(f"Profile {profile!r} dispatcher was {actual!r}, expected {expected!r}.")

        for source in cfg.sources:
            own = _run(("hermes", "-p", source.profile, "cron", "list", "--all"), env)
            job_name = f"{cfg.name}-{source.id}-scout"
            if job_name not in own:
                raise RuntimeError(f"Cron job {job_name!r} is absent from owner {source.profile!r}.")
            for other in cfg.hermes.profiles:
                if other == source.profile:
                    continue
                if job_name in _run(("hermes", "-p", other, "cron", "list", "--all"), env):
                    raise RuntimeError(f"Cron job {job_name!r} leaked into profile {other!r}.")

        report = run_preflight(cfg, runner=IsolatedRunner(env))
        expected_not_started = {
            *(f"resource.gateway.{name}" for name in [cfg.hermes.gateway_profile, *(s.profile for s in cfg.sources)]),
            *(f"resource.cron-status.{source.profile}" for source in cfg.sources),
            "resource.gate-channel",
        }
        unexpected_checks = [check for check in report.checks if not check.ok and check.name not in expected_not_started]
        toolset_failures = [
            check for check in unexpected_checks
            if check.name.startswith("resource.toolsets.")
            or check.name.startswith("capability.toolset-names.")
        ]
        inventories = _toolset_inventories(cfg, IsolatedRunner(env))
        runtime_toolset_blockers, product_toolset_failures = classify_runtime_toolset_failures(
            cfg,
            toolset_failures,
            inventories,
        )
        unexpected = [
            check.name for check in unexpected_checks
            if check not in toolset_failures
        ] + [check.name for check in product_toolset_failures]
        if unexpected:
            raise RuntimeError(f"Disposable preflight had unexpected blockers: {unexpected}")
        evidence.append("Board, profiles/descriptions, working directories, rendered skills, dispatcher flags, and profile-local cron jobs were verified.")
        evidence.append("Gateway, cron-running, and gate-channel checks remain blocked because the rehearsal never starts services or reads credentials.")

        auth_files = [path for path in (home / "hermes").rglob("auth.json")]
        credential_env: list[Path] = []
        for path in (home / "hermes").rglob(".env"):
            assignments = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#") and "=" in line
            ]
            if any(line.split("=", 1)[1].strip() for line in assignments):
                credential_env.append(path)
        if auth_files or credential_env:
            raise RuntimeError(
                "Disposable rehearsal unexpectedly created/copied credential data: "
                f"auth={auth_files}, env_with_values={credential_env}"
            )
        evidence.append("Hermes created only comment/empty-value profile .env stubs and no auth.json; no real credential data was read or copied.")
        evidence.append("No gateway, provider, authentication, or message command ran.")
        if runtime_toolset_blockers:
            evidence.append(
                "Hermes runtime toolset compatibility blocked a complete preflight: "
                + "; ".join(check.evidence for check in runtime_toolset_blockers)
            )
    evidence.append("Temporary HOME, HERMES_HOME, staging skills, board, profiles, and cron state were automatically removed.")
    return RehearsalReport(
        not runtime_toolset_blockers,
        tuple(evidence),
        runtime_compatibility_blocked=bool(runtime_toolset_blockers),
    )


if __name__ == "__main__":
    result = run_rehearsal()
    for line in result.evidence:
        print(f"[{'OK' if result.ok else 'BLOCKED'}] {line}")
    raise SystemExit(0 if result.ok else 1)
