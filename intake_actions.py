#!/usr/bin/env python3
"""Deterministic intake adapter — parse, dedup, apply scores, build the pre-gate graph.

This is the sanctioned path for the orchestrator's `intake:` and `triage:` task
modes. The model contributes exactly ONE judgment: per-dimension rubric scores.
Everything else — parsing, slugs, dedup, item creation, the triage root, the
evidence lanes, the classifier fan-in, the route card, idempotency keys, and
frontmatter links — is computed here from `triage.yaml` and the engine.

    python intake_actions.py --config triage.yaml plan   --intake <report.md>
    python intake_actions.py --config triage.yaml apply  --intake <report.md> \
        --scores <scores.json> --intake-task <task-id>
    python intake_actions.py --config triage.yaml verify --slug <slug>

Flow (see skills/templates/triage-orchestrator/SKILL.md):

  1. `plan` (read-only) parses the report, dedups each candidate against the
     vault, and prints the rubric prompt plus one entry per candidate.
  2. The orchestrator judges scores and writes `<report>.scores.json`:
     `{"scores": {"<slug>": {"<dimension>": <int>, ...}, ...}}`.
  3. `apply` validates the scores through the engine, creates/updates vault
     items, and builds the complete board graph for every advancing item.
     Re-running `apply` is idempotent: existing cards are found by
     idempotency key (interoperable with `hermes kanban`-created cards).
  4. `verify` (read-only board check) backs the `triage:` release barrier: it
     confirms the graph exists and creates nothing.

Never hand-write board cards, vault items, or slugs for these steps — a skill
or worker that needs something this adapter cannot do is a signal to extend
the adapter, not to write a one-off script.

Environment overrides: TRIAGE_CONFIG, TRIAGE_VAULT_DIR, HERMES_KANBAN_DB,
HERMES_HOME (see engine/kanban_store.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from engine.config import TriageConfig
from engine.engine import TriageEngine
from engine.intake_parser import Candidate, parse_intake_report
from engine.item_vault import ItemVault, slugify, utc_now_iso
from engine.kanban_store import KanbanStore
from proposal_actions import board_db, die_backend, die_input, die_state, vault_dir


def load_config(config_path: str | Path | None = None) -> TriageConfig:
    return TriageConfig.load(config_path or os.environ.get("TRIAGE_CONFIG") or "triage.yaml")


def _candidate_text(candidate: Candidate) -> str:
    return f"{candidate.title}\n{candidate.claim}"


def _item_body(candidate: Candidate) -> str:
    sources = "\n".join(
        f"- {src.get('url', '')}" + (f": {src.get('quote', '')}" if src.get("quote") else "")
        for src in candidate.sources
    )
    return (
        f"## Claim\n\n{candidate.claim}\n\n"
        f"## Why it may matter\n\n{candidate.why_it_may_matter}\n\n"
        f"## Sources\n\n{sources}\n"
    )


def _load_scores(path: Path) -> dict[str, dict[str, Any]]:
    """Accept the canonical `{"scores": {slug: breakdown}}` plus the two
    historical shapes workers produced, so nothing forces a hand-rolled bridge."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("scores"), dict):
        return {str(k): dict(v) for k, v in data["scores"].items()}
    if isinstance(data, dict) and isinstance(data.get("decisions"), list):
        return {
            str(entry["slug"]): dict(entry["score_breakdown"])
            for entry in data["decisions"]
            if entry.get("score_breakdown")
        }
    if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
        return {str(k): dict(v) for k, v in data.items()}
    raise ValueError(
        f"Unrecognized scores file shape in {path}; expected {{\"scores\": {{slug: breakdown}}}}."
    )


# --------------------------------------------------------------------------- #
# plan — read-only
# --------------------------------------------------------------------------- #


def action_plan(intake_path: str, config_path: str | Path | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    engine = TriageEngine(config, ItemVault(vault_dir(config)))
    report = parse_intake_report(Path(intake_path).read_text(encoding="utf-8"))

    candidates = []
    for candidate in report.candidates:
        slug = slugify(candidate.title)
        matches = engine.dedup(_candidate_text(candidate), top_k=3)
        top = matches[0] if matches else None
        is_duplicate = bool(top and top.decision == "duplicate" and top.slug != slug)
        candidates.append({
            "slug": slug,
            "title": candidate.title,
            "claim": candidate.claim,
            "sources": len(candidate.sources),
            "dedup_decision": top.decision if top else "new",
            "dedup_top": {"slug": top.slug, "score": top.score} if top else None,
            "needs_score": not is_duplicate,
        })
    return {
        "ok": True,
        "pipeline_id": config.pipeline_id,
        "intake": str(Path(intake_path).resolve()),
        "report_metadata": report.metadata,
        "rubric_prompt": engine.rubric_prompt(),
        "scores_file_shape": {"scores": {"<slug>": {"<dimension>": "<int 0..max>"}}},
        "candidates": candidates,
    }


# --------------------------------------------------------------------------- #
# apply — create items + the complete pre-gate board graph
# --------------------------------------------------------------------------- #


def _ensure_item(vault: ItemVault, candidate: Candidate, slug: str):
    try:
        return vault.create_item(
            slug=slug,
            title=candidate.title,
            sources=candidate.sources,
            body=_item_body(candidate),
        ), True
    except FileExistsError:
        return vault.load(slug), False


def _link_tasks(vault: ItemVault, item, task_ids: list[str]) -> None:
    links = item.frontmatter.setdefault("linked_kanban_tasks", [])
    for task_id in task_ids:
        if task_id not in links:
            links.append(task_id)
    vault.save(item)


def action_apply(
    intake_path: str,
    scores_path: str | None,
    intake_task_id: str,
    config_path: str | Path | None = None,
    *,
    heuristic: bool = False,
) -> dict[str, Any]:
    config = load_config(config_path)
    vault = ItemVault(vault_dir(config))
    engine = TriageEngine(config, vault)
    report = parse_intake_report(Path(intake_path).read_text(encoding="utf-8"))
    scores = _load_scores(Path(scores_path)) if scores_path else {}

    store = KanbanStore(board_db(config))
    conn = store.connect()
    decisions: list[dict[str, Any]] = []
    problems: list[str] = []
    try:
        for candidate in report.candidates:
            slug = slugify(candidate.title)
            entry: dict[str, Any] = {"slug": slug, "title": candidate.title}
            decisions.append(entry)

            matches = engine.dedup(_candidate_text(candidate), top_k=3)
            top = matches[0] if matches else None

            # A candidate can match ITS OWN item on re-run; that is idempotent
            # re-application, not a duplicate merge.
            if top and top.decision == "duplicate" and top.slug != slug:
                existing = vault.load(top.slug)
                known_urls = {s.get("url") for s in existing.frontmatter.get("sources", [])}
                added = [s for s in candidate.sources if s.get("url") not in known_urls]
                for src in added:
                    existing.frontmatter.setdefault("sources", []).append(src)
                existing.frontmatter["last_seen"] = utc_now_iso()
                vault.save(existing)
                entry.update({"action": "merged_duplicate", "into": top.slug, "sources_added": len(added)})
                continue

            # Score: model-judged breakdown validated by the engine (or the
            # deterministic heuristic for offline/synthetic runs).
            if heuristic:
                result = engine.score_heuristic({
                    "title": candidate.title,
                    "claim": candidate.claim,
                    "sources": candidate.sources,
                    "why_it_may_matter": candidate.why_it_may_matter,
                })
            elif slug in scores:
                result = engine.score(scores[slug])
            else:
                entry["action"] = "needs_score"
                problems.append(f"{slug}: no breakdown in scores file")
                continue

            item, created = _ensure_item(vault, candidate, slug)
            item.frontmatter["score"] = result.total
            item.frontmatter["score_breakdown"] = result.breakdown
            item.frontmatter["last_seen"] = utc_now_iso()
            if top and top.decision == "possible":
                item.frontmatter["possible_duplicate_of"] = top.slug
            entry.update({"created": created, "score": result.total, "score_notes": result.notes})

            if not result.advance:
                item.frontmatter["status"] = "shelved_below_threshold"
                vault.save(item)
                entry["action"] = "shelved_below_threshold"
                continue

            item.frontmatter["status"] = "triage"
            vault.save(item)

            # Board graph: root (barrier) → lanes ⇉ classifier → route.
            pid = config.pipeline_id
            root_spec = engine.triage_root_spec(slug, intake_task_id)
            root_id = store.create_task(
                conn,
                parents=root_spec.parents,
                created_by="hermes-triage:intake",
                idempotency_key=f"{pid}:triage:{intake_task_id}:{slug}",
                **root_spec.store_kwargs(config),
            )
            conn.commit()
            # Durable audit link BEFORE the fan-out, per docs/05.
            _link_tasks(vault, item, [root_id])

            lane_ids: list[str] = []
            for spec in engine.research_specs(slug, root_id):
                lane = spec.title.split(":", 1)[0].strip()
                lane_ids.append(store.create_task(
                    conn,
                    parents=spec.parents,
                    created_by="hermes-triage:intake",
                    idempotency_key=f"{pid}:research:{intake_task_id}:{slug}:{lane}",
                    **spec.store_kwargs(config),
                ))
            classifier_spec = engine.classifier_spec(slug, lane_ids)
            classifier_id = store.create_task(
                conn,
                parents=classifier_spec.parents,
                created_by="hermes-triage:intake",
                idempotency_key=(
                    f"{pid}:research:{intake_task_id}:{slug}:{config.research.classifier_lane}"
                ),
                **classifier_spec.store_kwargs(config),
            )
            route_spec = engine.route_spec(slug, classifier_id)
            route_id = store.create_task(
                conn,
                parents=route_spec.parents,
                created_by="hermes-triage:intake",
                idempotency_key=f"{pid}:route:{intake_task_id}:{slug}",
                **route_spec.store_kwargs(config),
            )
            conn.commit()
            _link_tasks(vault, item, [*lane_ids, classifier_id, route_id])
            entry.update({
                "action": "graph_created",
                "triage_task_id": root_id,
                "lane_task_ids": lane_ids,
                "classifier_task_id": classifier_id,
                "route_task_id": route_id,
            })
    finally:
        conn.close()

    result = {
        "ok": not problems,
        "pipeline_id": config.pipeline_id,
        "intake_task": intake_task_id,
        "decisions": decisions,
    }
    if problems:
        result["problems"] = problems
    return result


# --------------------------------------------------------------------------- #
# verify — read-only release-barrier check for `triage:` workers
# --------------------------------------------------------------------------- #


def action_verify(slug: str, config_path: str | Path | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    engine = TriageEngine(config, ItemVault(vault_dir(config)))
    lanes = [l for l in config.research.lanes if l != config.research.classifier_lane]

    store = KanbanStore(board_db(config))
    conn = store.connect()
    missing: list[str] = []
    try:
        root = store.find_active_by_title(conn, f"triage: {slug}")
        lane_ids: list[str] = []
        for lane in lanes:
            row = store.find_active_by_title(conn, f"{lane}: {slug}")
            if row is None:
                missing.append(f"lane `{lane}`")
            else:
                lane_ids.append(row["id"])
                if root is not None and root["id"] not in store.parent_ids(conn, row["id"]):
                    missing.append(f"lane `{lane}` is not parented to the triage root")
        classifier = store.find_active_by_title(
            conn, f"{config.research.classifier_lane}: {slug}"
        )
        if classifier is None:
            missing.append("classifier card")
        else:
            classifier_parents = set(store.parent_ids(conn, classifier["id"]))
            if not set(lane_ids) <= classifier_parents:
                missing.append("classifier is missing evidence-lane parent(s)")
        route = store.find_active_by_title(conn, f"route: {slug}")
        if route is None:
            missing.append("route card")
        elif classifier is not None and classifier["id"] not in store.parent_ids(conn, route["id"]):
            missing.append("route card is not parented to the classifier")
        return {
            "ok": not missing,
            "pipeline_id": config.pipeline_id,
            "slug": slug,
            "triage_task_id": root["id"] if root else None,
            "lane_task_ids": lane_ids,
            "classifier_task_id": classifier["id"] if classifier else None,
            "route_task_id": route["id"] if route else None,
            "missing": missing,
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="intake_actions", description=__doc__)
    parser.add_argument("--config", default=os.environ.get("TRIAGE_CONFIG") or "triage.yaml")
    sub = parser.add_subparsers(dest="action", required=True)

    p_plan = sub.add_parser("plan", help="Parse + dedup (read-only); prints the rubric prompt.")
    p_plan.add_argument("--intake", required=True)

    p_apply = sub.add_parser("apply", help="Apply scores; create items and the pre-gate graph.")
    p_apply.add_argument("--intake", required=True)
    p_apply.add_argument("--scores", default=None, help="JSON of model-judged breakdowns (see plan output).")
    p_apply.add_argument("--heuristic", action="store_true",
                         help="Use the deterministic reference-domain scorer instead of --scores.")
    p_apply.add_argument("--intake-task", default=os.environ.get("HERMES_KANBAN_TASK"))

    p_verify = sub.add_parser("verify", help="Read-only graph check for the triage release barrier.")
    p_verify.add_argument("--slug", required=True)

    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            result = action_plan(args.intake, args.config)
        elif args.action == "apply":
            if not args.intake_task:
                die_input("--intake-task is required outside a dispatched Kanban worker.")
            if not args.scores and not args.heuristic:
                die_input("apply needs --scores (model-judged breakdowns) or --heuristic.")
            result = action_apply(
                args.intake, args.scores, args.intake_task, args.config, heuristic=args.heuristic
            )
        else:
            result = action_verify(args.slug, args.config)
    except SystemExit:
        raise
    except (ValueError, FileNotFoundError) as exc:
        die_state(str(exc))
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        die_backend(f"Unhandled error: {type(exc).__name__}: {exc}")
        return 3
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
