"""Deterministic calculations and task-spec generation for the triage pipeline.

Design principle: **fat engine, thin skill.** Everything that can be computed
deterministically lives here in Python (testable, version-controlled). The
orchestrator skill (`skills/templates/triage-orchestrator/SKILL.md`) is reduced
to the few steps that genuinely need a model's JUDGMENT:

    - reading an item and proposing per-dimension rubric scores
    - producing the research classification
    - writing the proposal prose

This module owns dedup lookup, score validation, route resolution, research-lane
spec generation, ordered prep/fulfillment spec generation, and workspace choice.
It does not create the pre-gate route card or apply/link prep specs on the board;
those orchestration edges still live in the caller.

This module returns plain dataclass task specs (title/body/role/parents/workspace)
rather than touching the board itself, so it is unit-testable without a live DB.
`proposal_actions.py` and the orchestrator turn specs into real cards via
`KanbanStore.create_task`. See `docs/01-architecture.md`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Stage, TriageConfig
from .dedup import Match, similar_items
from .item_vault import ItemVault
from .routing import route_from_classification
from .scoring import (
    ScoreResult,
    rubric_prompt,
    score_candidate_heuristic,
    score_from_breakdown,
)


@dataclass
class TaskSpec:
    """A requested board card; the caller creates it and applies missing edges."""
    title: str
    body: str
    role: str                              # abstract role; map via config.role_to_profile()
    parents: list[str] = field(default_factory=list)
    workspace_kind: str = "scratch"
    workspace_path: str | None = None
    skills: list[str] = field(default_factory=list)   # skill names pinned on the card
    model_override: str | None = None                 # per-card model routing (roles/stage config)
    provider_override: str | None = None
    reasoning_effort: str | None = None

    def assignee(self, config: TriageConfig) -> str:
        return config.role_to_profile(self.role)

    def store_kwargs(self, config: TriageConfig) -> dict:
        """The exact keyword arguments adapters pass to KanbanStore.create_task."""
        return {
            "title": self.title,
            "body": self.body,
            "assignee": self.assignee(config),
            "workspace_kind": self.workspace_kind,
            "workspace_path": self.workspace_path,
            "skills": self.skills or None,
            "model_override": self.model_override,
            "provider_override": self.provider_override,
            "reasoning_effort": self.reasoning_effort,
        }


class TriageEngine:
    def __init__(self, config: TriageConfig, vault: ItemVault | None = None):
        self.config = config
        self.vault = vault or ItemVault(self._default_vault_dir())

    # ----- paths / locations ----- #

    def _default_vault_dir(self) -> Path:
        return self.config.workspace_path / "vault" / "items"

    def workspace_for(self, path_name: str, slug: str) -> Path:
        sub = self.config.get_path(path_name).workspace_subdir or path_name
        return self.config.workspace_path / sub / slug

    # ----- model routing ----- #

    def _model_overrides(self, role: str, stage: Stage | None = None) -> dict[str, str | None]:
        """Stage-level model routing wins over role-level; both are optional."""
        role_def = self.config.role_def(role)
        return {
            "model_override": (stage.model if stage and stage.model else role_def.model),
            "provider_override": (stage.provider if stage and stage.provider else role_def.provider),
            "reasoning_effort": (
                stage.reasoning_effort if stage and stage.reasoning_effort else role_def.reasoning_effort
            ),
        }

    # ----- stage 2: dedup ----- #

    def dedup(self, candidate_text: str, top_k: int = 3) -> list[Match]:
        d = self.config.dedup
        return similar_items(
            candidate_text, self.vault, top_k=top_k,
            duplicate_threshold=d.duplicate_threshold,
            possible_threshold=d.possible_threshold,
        )

    # ----- stage 3: scoring (two modes) ----- #

    def rubric_prompt(self) -> str:
        """The instruction to hand the orchestrator for LLM-mode scoring."""
        return rubric_prompt(self.config.rubric)

    def score(self, breakdown: dict[str, Any]) -> ScoreResult:
        """LLM mode: apply the configured maxes/threshold to a model breakdown."""
        return score_from_breakdown(breakdown, self.config.rubric)

    def score_heuristic(self, candidate: dict[str, Any]) -> ScoreResult:
        """Deterministic mode: score structured fields, no model call."""
        return score_candidate_heuristic(candidate, self.config.rubric)

    # ----- stage 4: research fan-out ----- #

    def research_specs(self, slug: str, triage_task_id: str) -> list[TaskSpec]:
        """Parallel evidence lanes, excluding the downstream classifier."""
        return [
            self._research_spec(slug, lane, [triage_task_id])
            for lane in self.config.research.lanes
            if lane != self.config.research.classifier_lane
        ]

    def classifier_spec(self, slug: str, evidence_task_ids: list[str]) -> TaskSpec:
        """Classifier fan-in that runs only after every evidence lane."""
        if not evidence_task_ids:
            raise ValueError("classifier requires at least one evidence task")
        return self._research_spec(
            slug, self.config.research.classifier_lane, evidence_task_ids, classifier=True
        )

    def _research_spec(
        self, slug: str, lane: str, parents: list[str], *, classifier: bool = False
    ) -> TaskSpec:
        role = self.config.research.profile_role
        guide = ""
        if self.config.research.guide:
            rel = self.config.research.guide
            guide_path = Path(rel)
            if not guide_path.is_absolute():
                guide_path = Path(self.config.hermes.project_root) / guide_path
            guide = (
                f"\n\n--- RESEARCH LANE GUIDE — from {rel} ---\n"
                f"{guide_path.read_text(encoding='utf-8')}\n"
                if guide_path.exists()
                else f"\n\n--- RESEARCH LANE GUIDE: referenced {rel} but file is MISSING. ---\n"
            )
        classifier_note = ""
        if classifier:
            vals = sorted(self.config.route.map)
            classifier_note = (
                f"\n\nThis lane is the CLASSIFIER and runs after all evidence lanes. "
                f"Read their parent results and return `{self.config.route.classifier}` "
                f"as one of: {vals}."
            )
        return TaskSpec(
            title=f"{lane}: {slug}",
            body=(
                f"Research lane `{lane}` for item `{slug}`.\n"
                f"Read the item file, do the lane's research, report findings."
                f"{classifier_note}{guide}"
            ),
            role=role,
            parents=parents,
            workspace_kind="dir",
            workspace_path=str(Path(self.config.hermes.project_root).resolve()),
            **self._model_overrides(role),
        )

    # ----- stage 4: pre-gate graph roots (triage barrier + route fan-in) ----- #

    def triage_root_spec(self, slug: str, intake_task_id: str) -> TaskSpec:
        """The per-item release barrier card, parented to the intake task.

        The parent edge keeps this root `todo` while the intake worker's adapter
        builds the full lane/classifier/route graph beneath it; the dispatcher
        cannot spawn a second orchestrator into a half-built graph. Its worker
        VERIFIES and completes itself — it never creates cards.
        """
        role = "orchestrator"
        return TaskSpec(
            title=f"triage: {slug}",
            body=(
                f"Release barrier for item `{slug}`. This is NOT a second orchestrator pass.\n\n"
                f"Verify the research graph that the intake adapter already created:\n\n"
                f"    python intake_actions.py --config \"{self.config.config_path}\" verify --slug {slug}\n\n"
                "If it reports ok, complete this task (kanban_complete) to release the evidence "
                "lanes together. Create NOTHING. If it reports missing pieces, block this task "
                "with the adapter's JSON output as the reason — do not rebuild the graph yourself."
            ),
            role=role,
            parents=[intake_task_id],
            workspace_kind="dir",
            workspace_path=str(Path(self.config.hermes.project_root).resolve()),
            **self._model_overrides(role),
        )

    def route_spec(self, slug: str, classifier_task_id: str) -> TaskSpec:
        """The route fan-in card, parented only to the classifier.

        The only judgment here is READING the classifier value from the parent
        result; path resolution and prep-chain creation are deterministic in
        `pre_gate_actions.py`.
        """
        role = "orchestrator"
        return TaskSpec(
            title=f"route: {slug}",
            body=(
                f"Route resolution for item `{slug}`.\n\n"
                f"Read the parent classifier's result and extract its "
                f"`{self.config.route.classifier}` value (one of "
                f"{sorted(self.config.route.map)}). Then run the deterministic adapter:\n\n"
                f"    python pre_gate_actions.py --config \"{self.config.config_path}\" {slug} "
                f"--classification \"<value>\" --route-task \"$HERMES_KANBAN_TASK\"\n\n"
                "It resolves the path, writes it on the item, creates the prep chain and the "
                "proposal card (or closes out an auto path such as shelve). Confirm the JSON "
                "response, then complete this task. Never create prep or proposal cards yourself. "
                "If the classifier result lacks the value, block this task with a reason."
            ),
            role=role,
            parents=[classifier_task_id],
            workspace_kind="dir",
            workspace_path=str(Path(self.config.hermes.project_root).resolve()),
            skills=[self.config.orchestrator_skill],
            **self._model_overrides(role),
        )

    # ----- stage 5: route ----- #

    def route(self, classification: str) -> str:
        return route_from_classification(classification, self.config.route)

    # ----- stages 6-7: pre-gate prep chain ----- #

    def prep_specs(self, slug: str, path_name: str) -> list[TaskSpec]:
        """Return ordered pre-gate prep specs; the caller must link them in order."""
        path = self.config.get_path(path_name)
        return self._chain(path.prep, slug, path_name, phase="prep", persistent=False)

    # ----- stages 9-11: post-gate fulfillment chain ----- #

    def fulfillment_specs(self, slug: str, path_name: str) -> list[TaskSpec]:
        """Return ordered post-approval specs in a shared persistent workspace.

        Every stage runs with workspace_kind="dir" pointed at the same per-item
        dir so artifacts (built code, slides, reports) survive between stages and
        the final delivery step can find them. Using scratch here strands the
        final step — this is a known, expensive footgun (see docs/05). The caller
        is responsible for creating the cards and linking each to its predecessor.
        """
        path = self.config.get_path(path_name)
        return self._chain(path.fulfill, slug, path_name, phase="fulfill", persistent=True)

    def _chain(self, stages: list[Stage], slug: str, path_name: str, *, phase: str, persistent: bool) -> list[TaskSpec]:
        specs: list[TaskSpec] = []
        path = self.config.get_path(path_name)
        ws_kind, ws_path = "scratch", None
        ws_note = ""
        if persistent:
            ws_dir = self.workspace_for(path_name, slug)
            ws_kind, ws_path = "dir", str(ws_dir)
            ws_note = (
                f"\nWorkspace: your cwd is the PERSISTENT dir `{ws_path}` (workspace_kind=dir). "
                "Write ALL artifacts here — never a scratch/tmp dir; later stages read this exact path.\n"
            )
            if path.deliverable:
                ws_note += (
                    f"Deliverable contract: the delivery adapter resolves `{path.deliverable}` against "
                    "this workspace ROOT (a non-recursive glob). The deliverable and its companion files "
                    "belong at exactly that path — never inside an extra wrapper directory such as "
                    "`build/` or `output/`.\n"
                )
        injected = self._injected_constraints(path)
        for i, stage in enumerate(stages):
            layout_note = ""
            if persistent and path.deliverable and i == 0 and i < len(stages) - 1:
                layout_note = (
                    f"\n--- DELIVERABLE LAYOUT CHECK (deterministic adapter) ---\n"
                    f"This stage produces the deliverable. Before completing this task, verify the "
                    f"workspace satisfies the delivery contract:\n\n"
                    f"    python delivery_actions.py --config \"{self.config.config_path}\" deliver {slug} --dry-run\n\n"
                    "It sends nothing and mutates nothing. If it exits non-zero, fix the workspace layout "
                    "so the configured deliverable resolves — do NOT edit triage.yaml — or block this "
                    "task with its JSON error.\n"
                )
            delivery_note = ""
            if persistent and i == len(stages) - 1:
                delivery_note = (
                    f"\n--- FINAL DELIVERY (deterministic adapter) ---\n"
                    f"This is the final fulfillment stage. Finish the stage's own work, then run:\n\n"
                    f"    python delivery_actions.py --config \"{self.config.config_path}\" deliver {slug}\n\n"
                    "The adapter locates the configured deliverable in this workspace and sends it to "
                    "the configured gate target as a single `hermes send` attachment message, then "
                    "records delivery on the item. Complete this task only after it returns ok. If it "
                    "exits non-zero, block this task with its JSON error — do NOT send manually, "
                    "retry in a loop, or improvise a path.\n"
                )
            body = (
                f"Stage `{stage.stage}` ({phase}) for item `{slug}` on the `{path_name}` path.\n"
                f"Read the item file for the approved proposal, sources, score, and human notes.\n"
                f"{ws_note}{layout_note}{delivery_note}{injected}"
            )
            specs.append(TaskSpec(
                title=f"{stage.stage}: {slug}",
                body=body,
                role=stage.role,
                # Specs carry no intra-chain ids because cards do not exist yet.
                # The applying caller links each later card to its predecessor.
                parents=[],
                workspace_kind=ws_kind,
                workspace_path=ws_path,
                **self._model_overrides(stage.role, stage),
            ))
        return specs

    def _injected_constraints(self, path) -> str:
        """Inline the path's scope-rails / deliverable-spec files so workers see them.

        Relative paths are resolved from the process cwd, which runtime commands
        are expected to set to hermes.project_root. We inline their contents into
        the task body so an isolated worker still sees the constraints.
        """
        out = []
        for label, rel in (("SCOPE RAILS (model-visible policy)", path.scope_rails),
                           ("DELIVERABLE SPEC (output format)", path.deliverable_spec)):
            if not rel:
                continue
            f = Path(rel)
            if not f.is_absolute():
                f = Path(self.config.hermes.project_root) / f
            if f.exists():
                out.append(f"\n--- {label} — from {rel} ---\n{f.read_text(encoding='utf-8')}\n")
            else:
                out.append(f"\n--- {label}: referenced {rel} but file is MISSING — fill it in. ---\n")
        return "".join(out)
