"""Load and validate `triage.yaml` — the single file that defines a pipeline.

THIS is the generalization. The engine code is generic; `triage.yaml` is your
domain. Everything domain-specific — what you detect, how you score it, where it
routes, what each path produces — is data here, not code.

Adapting the template = editing `triage.yaml` (and the markdown templates it
points at). You should not need to edit the engine to change your subject matter.

Reads YAML via PyYAML. That's the engine's one third-party dependency; if it is
missing this module raises a clear, actionable error. Skill materialization also
uses PyYAML to validate rendered frontmatter; item files use the stdlib
frontmatter reader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "triage.yaml is YAML; the config loader needs PyYAML.\n"
        "Install it:  pip install pyyaml\n"
        "(Config and skill rendering need it; item files use the stdlib reader.)"
    ) from exc


class ConfigError(ValueError):
    """Raised with a human/agent-readable message when triage.yaml is invalid."""


VERSION_RE = re.compile(
    r"\A(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
PIPELINE_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")


def parse_version_triplet(value: str) -> tuple[int, int, int] | None:
    """Parse the comparable release triplet from a semver-like version pin."""
    match = VERSION_RE.fullmatch(value.strip())
    return tuple(map(int, match.groups())) if match else None  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Typed view over the config. Each dataclass mirrors a block in triage.yaml.
# Keep field names == YAML keys so docs/03-config-reference.md stays 1:1.
# --------------------------------------------------------------------------- #


@dataclass
class Source:
    id: str
    profile: str          # the Hermes profile this scout runs under
    skill: str            # the scout skill name installed on that profile
    schedule: str         # cron expression registered in this source profile's local store
    query: str            # what the scout searches for (the domain prompt)


@dataclass
class RubricDimension:
    key: str
    max: int
    hint: str = ""        # guidance the orchestrator uses when scoring this dimension


@dataclass
class Rubric:
    threshold: int
    dimensions: list[RubricDimension]

    @property
    def max_total(self) -> int:
        return sum(d.max for d in self.dimensions)


@dataclass
class Stage:
    stage: str            # task title prefix, e.g. "prototype_build"
    role: str             # abstract role; mapped to a profile via `roles:`
    # Optional per-stage model routing; overrides the role-level values.
    model: str | None = None
    provider: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class RoleDef:
    """A role's concrete runtime binding: profile plus optional model routing.

    `roles:` values in triage.yaml may be a bare profile name (string) or a
    mapping `{profile, model, provider, reasoning_effort}`. Model routing is
    applied per created card via the board's `model_override` /
    `provider_override` / `reasoning_effort` columns — the profile itself is
    never mutated, so pipelines already running keep their behavior.
    """
    profile: str
    model: str | None = None
    provider: str | None = None
    reasoning_effort: str | None = None


@dataclass
class PathDef:
    name: str
    prep: list[Stage] = field(default_factory=list)       # runs BEFORE the human gate
    fulfill: list[Stage] = field(default_factory=list)    # runs AFTER approval
    propose_role: str = "orchestrator"                    # who drafts + sends the proposal
    proposal_template: str | None = None                  # markdown file under paths/
    workspace_subdir: str = ""                            # persistent dir bucket, e.g. "builds"
    scope_rails: str | None = None                        # prompt-policy md injected into workers
    deliverable_spec: str | None = None                   # output-format md injected into workers
    deliverable: str | None = None                        # primary deliverable file/glob inside the item workspace
    auto: bool = False                                    # True = terminal path (e.g. shelve), no work


@dataclass
class ResearchLanes:
    profile_role: str                # role the lanes run under (usually "researcher")
    lanes: list[str]                 # evidence lanes plus one downstream classifier lane
    classifier_lane: str             # the lane whose output the router reads
    guide: str | None = None         # optional shared lane instructions inlined into each task


@dataclass
class Route:
    classifier: str                  # dotted path into research output, e.g. "<lane>.solution_quality"
    map: dict[str, str]              # classification value -> path name


@dataclass
class Dedup:
    # Reserved backend selector. TriageEngine currently always uses token cosine.
    method: str = "token-cosine"
    duplicate_threshold: float = 0.62
    possible_threshold: float = 0.40


@dataclass
class Gate:
    channel: str = "discord"
    target: str | None = None
    approve: list[str] = field(default_factory=lambda: ["approve"])
    shelve: list[str] = field(default_factory=lambda: ["shelve", "reject the rest"])
    modify: list[str] = field(default_factory=lambda: ["modify"])


@dataclass(frozen=True)
class HermesProfile:
    """Deployment metadata for one Hermes profile."""

    description: str
    toolsets: tuple[str, ...] = ()
    owns_cron: bool = False
    shared: bool = False


@dataclass(frozen=True)
class HermesDeployment:
    """Validated, location-aware metadata used by the dry-run scaffolder."""

    min_version: str
    base_profile: str
    gateway_profile: str
    project_root: str
    profile_strategy: str
    max_spawn: int | None
    profiles: dict[str, HermesProfile]


@dataclass
class TriageConfig:
    name: str
    pipeline_id: str
    board: str
    workspace_root: str
    cost_gate_usd: float
    sources: list[Source]
    item_schema: list[str]
    dedup: Dedup
    rubric: Rubric
    research: ResearchLanes
    route: Route
    paths: dict[str, PathDef]
    roles: dict[str, str]              # role -> profile name (derived from role_defs)
    role_defs: dict[str, RoleDef]      # role -> full binding incl. optional model routing
    gate: Gate
    hermes: HermesDeployment
    config_path: str
    validation_warnings: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    # ----- convenience lookups the engine and proposal_actions use ----- #

    def role_to_profile(self, role: str) -> str:
        if role not in self.roles:
            raise ConfigError(
                f"Role {role!r} is used in a path/stage but is not defined in `roles:`. "
                f"Known roles: {sorted(self.roles)}."
            )
        return self.roles[role]

    def role_def(self, role: str) -> RoleDef:
        if role not in self.role_defs:
            raise ConfigError(
                f"Role {role!r} is used in a path/stage but is not defined in `roles:`. "
                f"Known roles: {sorted(self.role_defs)}."
            )
        return self.role_defs[role]

    def get_path(self, name: str) -> PathDef:
        if name not in self.paths:
            raise ConfigError(f"Path {name!r} is referenced but not defined under `paths:`.")
        return self.paths[name]

    @property
    def workspace_path(self) -> Path:
        path = Path(self.workspace_root)
        return (path if path.is_absolute() else Path(self.hermes.project_root) / path).resolve()

    @property
    def orchestrator_skill(self) -> str:
        return f"triage-{self.pipeline_id}"

    def scout_skill(self, source: Source) -> str:
        return f"{self.pipeline_id}-{source.skill}"

    def cron_name(self, source: Source) -> str:
        return f"{self.pipeline_id}-{source.id}-scout"

    @classmethod
    def load(cls, path: str | Path = "triage.yaml") -> "TriageConfig":
        p = Path(path)
        if not p.exists():
            raise ConfigError(
                f"Config not found: {p}. Pass the pipeline config explicitly "
                "(--config <file> or the TRIAGE_CONFIG env var)."
            )
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, config_path=p)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        config_path: str | Path | None = None,
    ) -> "TriageConfig":
        def req(key: str) -> Any:
            if key not in data:
                raise ConfigError(f"triage.yaml is missing required top-level key: `{key}`.")
            return data[key]

        rubric_d = req("rubric")
        rubric = Rubric(
            threshold=int(rubric_d["threshold"]),
            dimensions=[RubricDimension(**d) for d in rubric_d["dimensions"]],
        )
        research_d = req("research_lanes")
        research = ResearchLanes(
            profile_role=research_d.get("role", "researcher"),
            lanes=list(research_d["lanes"]),
            classifier_lane=research_d.get("classifier_lane", research_d["lanes"][-1]),
            guide=research_d.get("guide"),
        )
        route_d = req("route")
        route = Route(classifier=route_d["classifier"], map=dict(route_d["map"]))

        paths: dict[str, PathDef] = {}
        for name, pd in req("paths").items():
            pd = pd or {}
            paths[name] = PathDef(
                name=name,
                prep=[Stage(**s) for s in pd.get("prep", [])],
                fulfill=[Stage(**s) for s in pd.get("fulfill", [])],
                propose_role=(pd.get("propose") or {}).get("role", "orchestrator"),
                proposal_template=(pd.get("propose") or {}).get("template"),
                workspace_subdir=pd.get("workspace_subdir", name),
                scope_rails=pd.get("scope_rails"),
                deliverable_spec=pd.get("deliverable_spec"),
                deliverable=pd.get("deliverable"),
                auto=bool(pd.get("auto", False)),
            )

        role_defs: dict[str, RoleDef] = {}
        for role_name, role_value in req("roles").items():
            if isinstance(role_value, str):
                role_defs[str(role_name)] = RoleDef(profile=role_value)
            elif isinstance(role_value, dict):
                unknown = set(role_value) - {"profile", "model", "provider", "reasoning_effort"}
                if unknown:
                    raise ConfigError(
                        f"roles.{role_name} has unknown key(s) {sorted(unknown)}; "
                        "allowed: profile, model, provider, reasoning_effort."
                    )
                profile = str(role_value.get("profile") or "").strip()
                if not profile:
                    raise ConfigError(f"roles.{role_name} mapping form requires a non-empty `profile`.")
                role_defs[str(role_name)] = RoleDef(
                    profile=profile,
                    model=(str(role_value["model"]).strip() or None) if role_value.get("model") else None,
                    provider=(str(role_value["provider"]).strip() or None) if role_value.get("provider") else None,
                    reasoning_effort=(str(role_value["reasoning_effort"]).strip() or None) if role_value.get("reasoning_effort") else None,
                )
            else:
                raise ConfigError(
                    f"roles.{role_name} must be a profile name or a mapping with `profile:`; got {type(role_value).__name__}."
                )
        roles = {name: role_def.profile for name, role_def in role_defs.items()}
        sources = [Source(**s) for s in data.get("sources", [])]
        warnings: list[str] = []
        hermes_d = data.get("hermes")
        if hermes_d is None:
            # Temporary compatibility for configs created before deployment
            # metadata existed. The fallback is explicit about what it cannot
            # know instead of silently inventing least-privilege settings.
            warnings.append(
                "`hermes:` is absent; deployment profiles were derived from roles/sources, "
                "but descriptions and toolsets are unspecified. Add explicit `hermes.profiles` "
                "metadata before applying the scaffold plan."
            )
            source_profiles = {source.profile for source in sources}
            profile_names = sorted(set(roles.values()) | source_profiles)
            profiles = {
                name: HermesProfile(
                    description=f"Profile {name} (derived from legacy configuration).",
                    owns_cron=name in source_profiles,
                )
                for name in profile_names
            }
            gateway_profile = roles.get(
                "orchestrator",
                next(iter(roles.values()), profile_names[0] if profile_names else "orchestrator"),
            )
            root = (
                Path(config_path).resolve().parent
                if config_path is not None
                else Path.cwd().resolve()
            )
            hermes = HermesDeployment(
                min_version="0.20.0",
                base_profile="default",
                gateway_profile=gateway_profile,
                project_root=str(root),
                profile_strategy="clone",
                max_spawn=None,
                profiles=profiles,
            )
        else:
            project_root_value = str(hermes_d.get("project_root", "")).strip()
            project_root = Path(project_root_value)
            if not project_root_value:
                resolved_root = project_root
            elif project_root.is_absolute():
                resolved_root = project_root.resolve()
            elif config_path is None:
                raise ConfigError(
                    "Invalid triage.yaml:\n  - hermes.project_root is relative but no config file "
                    "location was supplied; use TriageConfig.load() or pass config_path."
                )
            else:
                resolved_root = (Path(config_path).resolve().parent / project_root).resolve()

            profiles = {}
            for name, profile_d in (hermes_d.get("profiles") or {}).items():
                profile_d = profile_d or {}
                profiles[str(name)] = HermesProfile(
                    description=str(profile_d.get("description", "")),
                    toolsets=tuple(str(value) for value in (profile_d.get("toolsets") or [])),
                    owns_cron=bool(profile_d.get("owns_cron", False)),
                    shared=bool(profile_d.get("shared", False)),
                )
            hermes = HermesDeployment(
                min_version=str(hermes_d.get("min_version", "")),
                base_profile=str(hermes_d.get("base_profile", "")),
                gateway_profile=str(hermes_d.get("gateway_profile", "")),
                project_root=str(resolved_root),
                profile_strategy=str(hermes_d.get("profile_strategy", "")),
                max_spawn=(int(hermes_d["max_spawn"]) if hermes_d.get("max_spawn") is not None else None),
                profiles=profiles,
            )

        cfg = cls(
            name=req("name"),
            pipeline_id=str(data.get("pipeline_id") or req("board")),
            board=req("board"),
            workspace_root=data.get("workspace_root", "./work"),
            cost_gate_usd=float(data.get("cost_gate_usd", 5)),
            sources=sources,
            item_schema=list((data.get("item_schema") or {}).get("fields", [])),
            dedup=Dedup(**(data.get("dedup") or {})),
            rubric=rubric,
            research=research,
            route=route,
            paths=paths,
            roles=roles,
            role_defs=role_defs,
            gate=Gate(**(data.get("gate") or {})),
            hermes=hermes,
            config_path=(
                str(Path(config_path).resolve())
                if config_path is not None
                else str(Path(hermes.project_root) / "triage.yaml")
            ),
            validation_warnings=tuple(warnings),
            raw=data,
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Fail loudly with actionable messages. Called by the CLI `validate`."""
        errors: list[str] = []

        if not PIPELINE_ID_RE.fullmatch(self.pipeline_id):
            errors.append(
                "pipeline_id must contain only lowercase letters, digits, underscores, or hyphens "
                "and must start with a letter or digit."
            )

        # Every route target must be a defined path.
        for value, target in self.route.map.items():
            if target not in self.paths:
                errors.append(f"route.map[{value!r}] -> {target!r}, but no such path under `paths:`.")

        # Every role used in any stage must be defined.
        used_roles = {self.research.profile_role}
        for p in self.paths.values():
            used_roles.add(p.propose_role)
            used_roles.update(s.role for s in p.prep + p.fulfill)
        for role in used_roles:
            if role not in self.roles:
                errors.append(f"Role {role!r} used by a path but missing from `roles:`.")

        # Rubric sanity.
        if self.rubric.threshold > self.rubric.max_total:
            errors.append(
                f"rubric.threshold ({self.rubric.threshold}) exceeds the sum of dimension maxes "
                f"({self.rubric.max_total}); nothing could ever pass."
            )

        # Classifier lane must exist.
        if self.research.classifier_lane not in self.research.lanes:
            errors.append(
                f"research_lanes.classifier_lane ({self.research.classifier_lane!r}) "
                f"is not one of the declared lanes {self.research.lanes}."
            )

        # Hermes deployment metadata and topology.
        if self.gate.target and not self.gate.target.startswith(f"{self.gate.channel}:"):
            errors.append(
                f"gate.target {self.gate.target!r} must use the gate.channel {self.gate.channel!r} prefix."
            )
        if not self.hermes.min_version:
            errors.append("hermes.min_version must be a non-empty version string.")
        elif parse_version_triplet(self.hermes.min_version) is None:
            errors.append(
                "hermes.min_version must be a semantic version such as '0.20.0', "
                "optionally with a prerelease or build suffix."
            )
        if not self.hermes.base_profile.strip():
            errors.append("hermes.base_profile must be non-empty.")
        if self.hermes.profile_strategy != "clone":
            errors.append(
                f"hermes.profile_strategy {self.hermes.profile_strategy!r} is unsupported; expected 'clone'."
            )
        if self.hermes.max_spawn is not None and self.hermes.max_spawn < 1:
            errors.append("hermes.max_spawn must be a positive integer when set.")
        if not self.hermes.project_root or not Path(self.hermes.project_root).is_absolute():
            errors.append("hermes.project_root must resolve to an absolute path from the config file location.")
        elif not Path(self.hermes.project_root).is_dir():
            errors.append(
                f"hermes.project_root does not resolve to an existing directory: {self.hermes.project_root!r}."
            )

        gateway = self.hermes.gateway_profile
        if gateway not in self.hermes.profiles or gateway not in set(self.roles.values()):
            errors.append(
                f"hermes.gateway_profile {gateway!r} must be present in both roles and hermes.profiles."
            )
        for role, profile_name in self.roles.items():
            if profile_name not in self.hermes.profiles:
                errors.append(
                    f"Role {role!r} maps to profile {profile_name!r}, which is missing from hermes.profiles."
                )
        seen_source_ids: set[str] = set()
        seen_profile_skills: set[tuple[str, str]] = set()
        for source in self.sources:
            if source.id in seen_source_ids:
                errors.append(
                    f"Duplicate sources[].id {source.id!r}; source IDs must be unique."
                )
            seen_source_ids.add(source.id)
            profile_skill = (source.profile, source.skill)
            if profile_skill in seen_profile_skills:
                errors.append(
                    "Duplicate sources[] (profile, skill) pair "
                    f"{profile_skill!r}; each rendered scout target must be unique."
                )
            seen_profile_skills.add(profile_skill)

        source_profiles = {source.profile for source in self.sources}
        for source in self.sources:
            if source.profile not in self.hermes.profiles:
                errors.append(
                    f"Source {source.id!r} uses profile {source.profile!r}, which is missing from hermes.profiles."
                )
            elif not self.hermes.profiles[source.profile].owns_cron:
                errors.append(
                    f"Source {source.id!r} uses profile {source.profile!r}, but that profile must set owns_cron=true."
                )
        for name, profile in self.hermes.profiles.items():
            if not profile.description.strip():
                errors.append(f"hermes.profiles[{name!r}].description must be non-empty.")
            if profile.owns_cron and name not in source_profiles:
                errors.append(
                    f"hermes.profiles[{name!r}] owns_cron=true but no source uses that profile."
                )

        if errors:
            raise ConfigError("Invalid triage.yaml:\n  - " + "\n  - ".join(errors))
