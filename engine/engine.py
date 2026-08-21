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

    def assignee(self, config: TriageConfig) -> str:
        return config.role_to_profile(self.role)


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
        injected = self._injected_constraints(path)
        for i, stage in enumerate(stages):
            body = (
                f"Stage `{stage.stage}` ({phase}) for item `{slug}` on the `{path_name}` path.\n"
                f"Read the item file for the approved proposal, sources, score, and human notes.\n"
                f"{ws_note}{injected}"
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
