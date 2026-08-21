"""Pure Hermes deployment planning and side-effect-free rendering.

This module deliberately does not import or invoke subprocess. It turns validated
configuration into reviewable data; execution remains a human decision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import shlex

from .config import Source, TriageConfig
from .skill_materialization import skill_targets


PHASES = (
    "runtime preflight",
    "board",
    "profiles",
    "profile working directories",
    "toolsets",
    "skills",
    "model/auth checkpoints",
    "cron",
    "gateways",
    "smoke verification",
)


@dataclass(frozen=True)
class CommandStep:
    phase: str
    description: str
    argv: tuple[str, ...]
    profile: str | None = None
    mutates: bool = True
    requires_human: bool = False
    verification: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManualCheckpoint:
    phase: str
    description: str
    verification: tuple[str, ...]
    # Retained for a stable serialized step shape; renderers execute argv only on CommandStep.
    argv: None = None
    profile: str | None = None
    mutates: bool = False
    requires_human: bool = True


PlanStep = CommandStep | ManualCheckpoint


@dataclass(frozen=True)
class DeploymentPlan:
    steps: tuple[PlanStep, ...]


def _cron_prompt(cfg: TriageConfig, source: Source) -> str:
    query = source.query.strip()
    return (
        f"Run the {cfg.scout_skill(source)} skill for source {source.id!r} in pipeline {cfg.pipeline_id!r}.\n"
        f"Search instructions:\n{query}\n"
        f"Create intake tasks on the {cfg.board!r} board for qualifying items."
    )


def build_deployment_plan(cfg: TriageConfig) -> DeploymentPlan:
    """Return the complete ordered deployment plan without external effects."""
    root = cfg.hermes.project_root
    gateway = cfg.hermes.gateway_profile
    steps: list[PlanStep] = []

    steps.append(
        ManualCheckpoint(
            phase="runtime preflight",
            description="Confirm the installed Hermes runtime supports this deployment plan.",
            verification=(
                f"Hermes is installed and its version is at least {cfg.hermes.min_version}.",
                "Confirm preflight passes capability.cron-tool-surface and the configured toolset-name checks before executing any generated cron-platform toolset command.",
                "Confirm the command surfaces used below before applying any mutating step.",
            ),
        )
    )

    steps.append(
        CommandStep(
            phase="board",
            description=f"Create the dedicated {cfg.board!r} Kanban board.",
            argv=(
                "hermes", "kanban", "boards", "create", cfg.board,
                "--name", cfg.name,
                "--description", f"Dedicated board for the {cfg.name} pipeline.",
                "--default-workdir", root,
            ),
        )
    )
    for name, profile in cfg.hermes.profiles.items():
        if name == cfg.hermes.base_profile or profile.shared:
            continue
        steps.append(
            CommandStep(
                phase="profiles",
                description=f"Create Hermes profile {name!r} from the configured base profile.",
                argv=(
                    "hermes", "profile", "create", name,
                    "--clone-from", cfg.hermes.base_profile,
                    "--no-alias",
                    "--description", profile.description,
                ),
                profile=name,
            )
        )
    for name in cfg.hermes.profiles:
        if name == cfg.hermes.base_profile or cfg.hermes.profiles[name].shared:
            continue
        steps.append(
            CommandStep(
                phase="profile working directories",
                description=f"Set the absolute working directory for profile {name!r}.",
                argv=("hermes", "-p", name, "config", "set", "terminal.cwd", root),
                profile=name,
            )
        )

    toolset_commands = 0
    for name, profile in cfg.hermes.profiles.items():
        if not profile.toolsets:
            continue
        toolset_commands += 1
        steps.append(
            CommandStep(
                phase="toolsets",
                description=f"Enable configured toolsets for interactive CLI sessions on {name!r}.",
                argv=("hermes", "-p", name, "tools", "enable", *profile.toolsets, "--platform", "cli"),
                profile=name,
            )
        )
        if profile.owns_cron:
            steps.append(
                CommandStep(
                    phase="toolsets",
                    description=f"Enable configured toolsets for cron-run sessions on {name!r}.",
                    argv=(
                        "hermes", "-p", name, "tools", "enable", *profile.toolsets,
                        "--platform", "cron",
                    ),
                    profile=name,
                )
            )
    if not toolset_commands:
        steps.append(
            ManualCheckpoint(
                phase="toolsets",
                description="Legacy configuration does not specify profile toolsets.",
                verification=("Add explicit hermes.profiles toolsets before applying this plan.",),
            )
        )

    skill_checks = [
        f"Review local {target.path} then manually copy it to {target.live_destination}."
        for target in skill_targets(cfg)
    ]
    skill_checks.append("Alternatively, package the rendered profile tree as a tested Hermes profile distribution.")
    skill_checks.append("Automatic `hermes profile install` remains deferred; scaffold does not write live profile directories.")
    steps.append(
        ManualCheckpoint(
            phase="skills",
            description="Prepare versioned skills before scheduling workers.",
            verification=tuple(skill_checks),
        )
    )

    steps.append(
        ManualCheckpoint(
            phase="model/auth checkpoints",
            description="Select models/providers and authenticate each profile without storing credentials in this repository.",
            verification=tuple(
                f"Verify model/provider selection and provider authentication for profile {name!r}."
                for name in cfg.hermes.profiles
            ),
        )
    )
    steps.append(
        ManualCheckpoint(
            phase="model/auth checkpoints",
            description=f"Authenticate the human gate channel {cfg.gate.channel!r} on the gateway profile.",
            verification=(
                f"Verify {cfg.gate.channel!r} delivery and reply handling for profile {gateway!r} without committing credentials.",
            ),
            profile=gateway,
        )
    )

    steps.append(
        CommandStep(
            phase="cron",
            description="Enable Kanban dispatch only on the configured gateway profile.",
            argv=("hermes", "-p", gateway, "config", "set", "kanban.dispatch_in_gateway", "true"),
            profile=gateway,
        )
    )
    if cfg.hermes.max_spawn is not None:
        steps.append(
            CommandStep(
                phase="cron",
                description=f"Cap concurrent Kanban workers at {cfg.hermes.max_spawn}.",
                argv=("hermes", "-p", gateway, "config", "set", "kanban.max_spawn", str(cfg.hermes.max_spawn)),
                profile=gateway,
            )
        )
    cron_profiles = {
        name for name, profile in cfg.hermes.profiles.items() if profile.owns_cron
    }
    for name in cfg.hermes.profiles:
        if name not in cron_profiles or name == gateway:
            continue
        steps.append(
            CommandStep(
                phase="cron",
                description=f"Disable Kanban dispatch on cron-owning scout scheduler gateway {name!r} (no messaging channel).",
                argv=("hermes", "-p", name, "config", "set", "kanban.dispatch_in_gateway", "false"),
                profile=name,
            )
        )
    for source in cfg.sources:
        if source.profile not in cron_profiles:
            continue
        steps.append(
            CommandStep(
                phase="cron",
                description=f"Create the profile-local scheduled scout for source {source.id!r}.",
                argv=(
                    "hermes", "-p", source.profile, "cron", "create",
                    source.schedule, _cron_prompt(cfg, source),
                    "--name", cfg.cron_name(source),
                    "--skill", cfg.scout_skill(source),
                    "--workdir", root,
                    "--deliver", "local",
                ),
                profile=source.profile,
            )
        )

    gateway_profiles = (
        []
        if gateway == cfg.hermes.base_profile or cfg.hermes.profiles[gateway].shared
        else [gateway]
    )
    gateway_profiles.extend(
        name for name in cfg.hermes.profiles
        if name in cron_profiles and name != gateway and not cfg.hermes.profiles[name].shared
    )
    for name in gateway_profiles:
        steps.append(
            CommandStep(
                phase="gateways",
                description=f"Install and start the gateway for profile {name!r}.",
                argv=("hermes", "-p", name, "gateway", "install", "--start-now", "--start-on-login"),
                profile=name,
            )
        )

    steps.append(
        ManualCheckpoint(
            phase="smoke verification",
            description="Verify the deployed topology before relying on scheduled intake.",
            verification=(
                f"Confirm exactly one Kanban dispatcher is active: profile {gateway!r}.",
                "Confirm the configured messaging gateway is running, and each cron-owning scout scheduler gateway is running without messaging credentials and with its profile-local cron registered.",
                f"Run one scout manually and verify it creates an intake task on board {cfg.board!r}.",
                f"Verify the human gate can deliver through {cfg.gate.channel!r} and receive a non-slash reply.",
            ),
        )
    )

    return DeploymentPlan(tuple(steps))


def render_shell(plan: DeploymentPlan) -> str:
    """Render a reviewable POSIX-shell plan with safe argument quoting."""
    lines = ["# Hermes deployment plan (dry run; review before executing)"]
    previous_phase: str | None = None
    for step in plan.steps:
        if step.phase != previous_phase:
            lines.extend(("", f"# === {step.phase} ==="))
            previous_phase = step.phase
        if isinstance(step, CommandStep):
            lines.append(f"# {step.description}")
            lines.append(shlex.join(step.argv))
        else:
            lines.append(f"# CHECKPOINT: {step.description}")
            lines.extend(f"#   - {item}" for item in step.verification)
    return "\n".join(lines) + "\n"


def render_json(plan: DeploymentPlan) -> str:
    """Render every command/checkpoint field for machine review."""
    return json.dumps([asdict(step) for step in plan.steps], indent=2) + "\n"
