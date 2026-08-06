"""Deterministically render reviewed, repository-local Hermes skills."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from .config import Source, TriageConfig


ORCHESTRATOR_SKILL = "triage-orchestrator"
PLACEHOLDER_RE = re.compile(
    r"\bTODO\s*:|<!--\s*TODO\b|\{\{[^}]+\}\}|<your\b|<source-id>|<board from",
    re.IGNORECASE,
)


class SkillTemplateError(ValueError):
    """Raised when a template cannot produce a safe installable skill."""


@dataclass(frozen=True)
class SkillTarget:
    profile: str
    skill: str
    path: Path
    live_destination: str
    source_id: str | None = None


def _workspace_root(cfg: TriageConfig) -> Path:
    configured = Path(cfg.workspace_root)
    if not configured.is_absolute():
        configured = Path(cfg.hermes.project_root) / configured
    return configured.resolve()


def skill_targets(cfg: TriageConfig, output_root: str | Path | None = None) -> tuple[SkillTarget, ...]:
    """Return exact staging and reviewed-copy destinations without writing."""
    staging = (
        Path(output_root).resolve()
        if output_root is not None
        else Path(cfg.hermes.project_root) / "work" / "scaffold" / "profiles"
    )
    targets = [
        SkillTarget(
            profile=source.profile,
            skill=source.skill,
            source_id=source.id,
            path=(staging / source.profile / "skills" / source.skill / "SKILL.md").resolve(),
            live_destination=f"$HERMES_HOME/profiles/{source.profile}/skills/{source.skill}/SKILL.md",
        )
        for source in cfg.sources
    ]
    profile = cfg.hermes.gateway_profile
    targets.append(
        SkillTarget(
            profile=profile,
            skill=ORCHESTRATOR_SKILL,
            path=(staging / profile / "skills" / ORCHESTRATOR_SKILL / "SKILL.md").resolve(),
            live_destination=f"$HERMES_HOME/profiles/{profile}/skills/{ORCHESTRATOR_SKILL}/SKILL.md",
        )
    )
    return tuple(targets)


def _render(template: str, replacements: dict[str, str], *, expected_name: str) -> str:
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace("{{" + token + "}}", value)
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", rendered, re.DOTALL)
    if not match:
        raise SkillTemplateError(f"Rendered skill {expected_name!r} has no YAML frontmatter.")
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillTemplateError(f"Rendered skill {expected_name!r} has invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict) or frontmatter.get("name") != expected_name:
        raise SkillTemplateError(
            f"Rendered skill frontmatter name must be {expected_name!r}; got "
            f"{frontmatter.get('name') if isinstance(frontmatter, dict) else None!r}."
        )
    placeholder = PLACEHOLDER_RE.search(rendered)
    if placeholder:
        raise SkillTemplateError(
            f"Rendered skill {expected_name!r} retains template placeholder {placeholder.group(0)!r}."
        )
    return rendered.rstrip() + "\n"


def _scout_replacements(cfg: TriageConfig, source: Source) -> dict[str, str]:
    return {
        "SKILL_NAME": source.skill,
        "SOURCE_ID": source.id,
        "BOARD": cfg.board,
        "QUERY": source.query.strip(),
        "INTAKE_DIR": str(_workspace_root(cfg) / "vault" / "intake"),
        "PROJECT_ROOT": cfg.hermes.project_root,
        "PROFILE": source.profile,
    }


def materialize_skills(
    cfg: TriageConfig,
    *,
    output_root: str | Path | None = None,
    template_root: str | Path | None = None,
) -> tuple[SkillTarget, ...]:
    """Render all configured skills into repository-local staging only."""
    templates = (
        Path(template_root).resolve()
        if template_root is not None
        else Path(cfg.hermes.project_root) / "skills" / "templates"
    )
    scout_template = (templates / "triage-scout" / "SKILL.md").read_text(encoding="utf-8")
    orchestrator_template = (templates / ORCHESTRATOR_SKILL / "SKILL.md").read_text(encoding="utf-8")
    targets = skill_targets(cfg, output_root)
    sources = {source.id: source for source in cfg.sources}
    for target in targets:
        if target.source_id is not None:
            rendered = _render(
                scout_template,
                _scout_replacements(cfg, sources[target.source_id]),
                expected_name=target.skill,
            )
        else:
            rendered = _render(
                orchestrator_template,
                {"BOARD": cfg.board, "PROJECT_ROOT": cfg.hermes.project_root},
                expected_name=target.skill,
            )
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(rendered, encoding="utf-8")
    return targets
