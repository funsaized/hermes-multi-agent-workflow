#!/usr/bin/env python3
"""Deterministically apply pre-gate task specs to the configured Hermes board."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from engine.config import TriageConfig
from engine.engine import TriageEngine
from engine.item_vault import ItemVault
from engine.kanban_store import KanbanStore
from proposal_actions import board_db, vault_dir


def proposal_body(config: TriageConfig, slug: str, path_name: str) -> str:
    path = config.get_path(path_name)
    item_path = vault_dir(config) / f"{slug}.md"
    template_path = Path(path.proposal_template or "")
    if not template_path.is_absolute():
        template_path = Path(config.hermes.project_root) / template_path
    output_path = config.workspace_path / "proposals" / f"{slug}.md"
    reference = f"{config.pipeline_id}:{slug}"
    return f"""Draft and deliver the human-gate proposal for `{slug}`.

All configured prep tasks are parents of this card and have finished. Read their
results, the item at `{item_path}`, and template `{template_path}`. Render the
completed proposal to `{output_path}`. The reply line must use the pipeline-qualified
reference exactly: `approve {reference} | shelve {reference}: <reason> | modify
{reference}: <change>`.

Set the item's frontmatter `status` to `awaiting_approval`, then send the proposal
with `hermes send --to {config.gate.target} --file "{output_path}"`. Setting status
alone is not delivery. Do not approve or create fulfillment cards; complete only
this proposal task after the send succeeds.
"""


def apply_prep(slug: str, route_task_id: str, config_path: str | None = None) -> dict:
    config = TriageConfig.load(config_path or os.environ.get("TRIAGE_CONFIG") or "triage.yaml")
    vault = ItemVault(vault_dir(config))
    item = vault.load(slug)
    path_name = item.frontmatter.get("path")
    if path_name not in config.paths:
        raise ValueError(f"Item {slug} has invalid path {path_name!r}.")

    specs = TriageEngine(config, vault).prep_specs(slug, path_name)
    store = KanbanStore(board_db(config))
    conn = store.connect()
    created = []
    try:
        parent = route_task_id
        for spec in specs:
            task_id = store.create_task(
                conn,
                title=spec.title,
                body=spec.body,
                assignee=spec.assignee(config),
                parents=[parent],
                created_by="hermes-triage:pre-gate",
                workspace_kind=spec.workspace_kind,
                workspace_path=spec.workspace_path,
                idempotency_key=f"{config.pipeline_id}:prep:{route_task_id}:{slug}:{spec.title}",
            )
            created.append({"task_id": task_id, "title": spec.title, "assignee": spec.assignee(config)})
            parent = task_id
        path = config.get_path(path_name)
        proposal_id = store.create_task(
            conn,
            title=f"propose: {slug}",
            body=proposal_body(config, slug, path_name),
            assignee=config.role_to_profile(path.propose_role),
            parents=[parent],
            created_by="hermes-triage:pre-gate",
            workspace_kind="dir",
            workspace_path=str(Path(config.hermes.project_root).resolve()),
            idempotency_key=f"{config.pipeline_id}:propose:{route_task_id}:{slug}",
        )
        proposal = {"task_id": proposal_id, "title": f"propose: {slug}", "assignee": config.role_to_profile(path.propose_role)}
        conn.commit()
    finally:
        conn.close()

    links = item.frontmatter.setdefault("linked_kanban_tasks", [])
    for card in [*created, proposal]:
        if card["task_id"] not in links:
            links.append(card["task_id"])
    vault.save(item)
    return {
        "ok": True,
        "pipeline_id": config.pipeline_id,
        "slug": slug,
        "path": path_name,
        "chain": created,
        "proposal_task_id": proposal["task_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("TRIAGE_CONFIG") or "triage.yaml")
    parser.add_argument("slug")
    parser.add_argument("--route-task", default=os.environ.get("HERMES_KANBAN_TASK"))
    args = parser.parse_args()
    if not args.route_task:
        parser.error("--route-task is required outside a dispatched Kanban worker")
    print(json.dumps(apply_prep(args.slug, args.route_task, args.config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
