#!/usr/bin/env python3
"""Synthetic end-to-end eval — replay a full pipeline cycle with no model, no
live Hermes, and no network.

Drives the DETERMINISTIC spine through every stage on a temp board that uses
the live Hermes 0.20 schema: intake plan/apply (scores play the model's one
judgment), the triage release barrier, dispatcher fan-in simulation, route
resolution (real path, auto path, and unknown-value failure), the human gate,
the fulfillment chain, and the delivery hook (with `hermes send` stubbed and
the composed command captured).

    python scripts/run_synthetic_eval.py [--report <path>]

Prints one line per scenario and writes a JSON report (default:
`work/eval/synthetic-report.json`). Exit code 0 only when every scenario
passes. Run it after any change to the engine, an adapter, or a skill
template — it is the regression harness for the pipeline's SHAPE.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import delivery_actions  # noqa: E402
import intake_actions  # noqa: E402
import pre_gate_actions  # noqa: E402
import proposal_actions  # noqa: E402
from engine.config import TriageConfig  # noqa: E402
from engine.item_vault import ItemVault  # noqa: E402
from tests.pipeline_fixtures import (  # noqa: E402
    make_pipeline,
    mark_done,
    write_intake_report,
)

SCORES = {
    "scores": {
        "build-a-widget-frobnicator": {"frequency": 25, "intensity": 25},
        "minor-cosmetic-nit": {"frequency": 2, "intensity": 3},
    }
}
SLUG = "build-a-widget-frobnicator"


class Scenario:
    def __init__(self):
        self.results: list[dict] = []

    @contextmanager
    def check(self, name: str):
        entry = {"scenario": name, "ok": False, "detail": None}
        self.results.append(entry)
        try:
            yield entry
            entry["ok"] = True
        except AssertionError as exc:
            entry["detail"] = f"ASSERTION: {exc}"
        except Exception as exc:  # noqa: BLE001 - report, don't crash the harness
            entry["detail"] = f"{type(exc).__name__}: {exc}"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def task_rows(db: Path, sql: str, *args):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run(report_path: Path | None = None) -> dict:
    s = Scenario()
    tmp = tempfile.TemporaryDirectory(prefix="synthetic-eval-")
    root = Path(tmp.name)
    config_path, db_path = make_pipeline(root)
    report = write_intake_report(root)
    scores_path = root / "scores.json"
    scores_path.write_text(json.dumps(SCORES), encoding="utf-8")

    env = {"HERMES_KANBAN_DB": str(db_path)}
    with patch.dict(os.environ, env):
        os.environ.pop("TRIAGE_VAULT_DIR", None)
        config = TriageConfig.load(config_path)
        vault = ItemVault(root / "work" / "vault" / "items")

        # ---- 1. intake: plan (read-only) + apply (items + graph) ---- #
        graph = {}
        with s.check("intake plan is read-only and canonical"):
            plan = intake_actions.action_plan(str(report), config_path)
            expect(plan["ok"], "plan not ok")
            expect(len(plan["candidates"]) == 3, "expected 3 parsed candidates")
            expect(
                task_rows(db_path, "SELECT COUNT(*) AS n FROM tasks")[0]["n"] == 0,
                "plan must create no cards",
            )
        with s.check("intake apply: graph + shelve + duplicate merge"):
            applied = intake_actions.action_apply(str(report), str(scores_path), "t_intake1", config_path)
            expect(applied["ok"], f"apply not ok: {applied}")
            by_slug = {d["slug"]: d for d in applied["decisions"]}
            expect(by_slug[SLUG]["action"] == "graph_created", "advancing item lacks graph")
            expect(by_slug["minor-cosmetic-nit"]["action"] == "shelved_below_threshold", "low scorer not shelved")
            expect(by_slug["build-the-widget-frobnicator"]["action"] == "merged_duplicate", "duplicate not merged")
            graph = by_slug[SLUG]
        with s.check("intake apply is idempotent on re-run"):
            before = task_rows(db_path, "SELECT COUNT(*) AS n FROM tasks")[0]["n"]
            again = intake_actions.action_apply(str(report), str(scores_path), "t_intake1", config_path)
            after = task_rows(db_path, "SELECT COUNT(*) AS n FROM tasks")[0]["n"]
            expect(again["ok"] and before == after, f"re-run changed the board: {before} -> {after}")
        with s.check("role model line lands on lane cards"):
            lanes = task_rows(
                db_path,
                "SELECT model_override, reasoning_effort FROM tasks WHERE id IN (%s)"
                % ",".join("?" * len(graph["lane_task_ids"])),
                *graph["lane_task_ids"],
            )
            expect(
                all(l["model_override"] == "cheap-model-1" and l["reasoning_effort"] == "low" for l in lanes),
                "researcher model line missing on lanes",
            )

        # ---- 2. triage release barrier ---- #
        with s.check("triage verify passes on the complete graph"):
            verified = intake_actions.action_verify(SLUG, config_path)
            expect(verified["ok"], f"verify failed: {verified['missing']}")
        # Dispatcher simulation: intake done → root promotes; root done → lanes run.
        mark_done(db_path, ["t_intake1", graph["triage_task_id"], *graph["lane_task_ids"], graph["classifier_task_id"]])

        # ---- 3. route ---- #
        with s.check("unknown classification fails loudly"):
            try:
                pre_gate_actions.apply_prep(SLUG, graph["route_task_id"], str(config_path), "weird")
                expect(False, "unknown classification did not raise")
            except ValueError:
                pass
        prep = {}
        with s.check("route builds ordered prep chain + proposal card"):
            prep = pre_gate_actions.apply_prep(SLUG, graph["route_task_id"], str(config_path), "missing")
            expect(prep["ok"] and prep["path"] == "build", f"bad route result: {prep}")
            expect(len(prep["chain"]) == 1 and prep["chain"][0]["assignee"] == "analyst", "prep chain wrong")
            parents = task_rows(
                db_path, "SELECT parent_id FROM task_links WHERE child_id = ?", prep["proposal_task_id"]
            )
            expect(
                [p["parent_id"] for p in parents] == [prep["chain"][0]["task_id"]],
                "proposal not parented to final prep card",
            )
            expect(vault.load(SLUG).frontmatter["path"] == "build", "path not written on item")

        # ---- 4. auto path (shelve) via a second advancing item ---- #
        with s.check("auto path closes out without cards"):
            report_b = write_intake_report(root, name="2026-08-21T11-00-00Z-web.md", text=(
                "source: web\ncaptured_at: 2026-08-21T11:00:00Z\n\n"
                "## Candidate: Completely unrelated gizmo calibration\n"
                "Claim: Gizmo calibration drifts weekly on production lines.\n"
                "Sources:\n  - url: https://example.com/gizmo\n    quote: \"drifts\"\n"
                "Why it may matter: Costly recalibration.\n"
            ))
            scores_b = root / "scores-b.json"
            scores_b.write_text(json.dumps(
                {"scores": {"completely-unrelated-gizmo-calibration": {"frequency": 25, "intensity": 25}}}
            ), encoding="utf-8")
            applied_b = intake_actions.action_apply(str(report_b), str(scores_b), "t_intake2", config_path)
            entry_b = applied_b["decisions"][0]
            expect(entry_b["action"] == "graph_created", f"gizmo item did not advance: {entry_b}")
            count_before = task_rows(db_path, "SELECT COUNT(*) AS n FROM tasks")[0]["n"]
            shelved = pre_gate_actions.apply_prep(
                "completely-unrelated-gizmo-calibration", entry_b["route_task_id"], str(config_path), "good"
            )
            count_after = task_rows(db_path, "SELECT COUNT(*) AS n FROM tasks")[0]["n"]
            expect(shelved["auto"] and shelved["path"] == "shelve", f"not auto-shelved: {shelved}")
            expect(count_before == count_after, "auto path must create no cards")
            expect(
                vault.load("completely-unrelated-gizmo-calibration").frontmatter["status"] == "auto_shelve",
                "auto path status not recorded",
            )

        # ---- 5. proposal send: one attachment message, bounded 429 backoff ---- #
        with s.check("proposal send: long proposal rides as ONE attachment message"):
            proposal_path = root / "work" / "proposals" / f"{SLUG}.md"
            proposal_path.parent.mkdir(parents=True, exist_ok=True)
            # Far beyond any 2000-char text limit — must still be a single send.
            proposal_path.write_text("# Proposal\n" + "x" * 17000, encoding="utf-8")
            responses = [
                Mock(returncode=1, stdout="", stderr="Discord API error (429): retry_after 0.4s"),
                Mock(returncode=0, stdout="", stderr=""),
            ]
            with (
                patch.object(delivery_actions.shutil, "which", return_value="hermes"),
                patch.object(delivery_actions.subprocess, "run", side_effect=responses) as send_cmd,
                patch.object(delivery_actions.time, "sleep") as slept,
            ):
                sent_prop = delivery_actions.action_send_proposal(SLUG, config_path)
            expect(sent_prop["ok"] and sent_prop["send_attempts"] == 2, f"proposal send failed: {sent_prop}")
            expect(send_cmd.call_count == 2, "429 must be retried exactly once here")
            message = send_cmd.call_args.args[0][-1]
            expect("MEDIA:" in message, "proposal not sent as a native attachment")
            expect(len(sent_prop["caption"]) <= delivery_actions.GATE_CAPTION_LIMIT,
                   "caption exceeds the single-message cap")
            expect(slept.call_args.args[0] == delivery_actions.RATE_LIMIT_RETRY_DELAYS[0],
                   "429 backoff not applied")
            expect(vault.load(SLUG).frontmatter["status"] == "awaiting_approval",
                   "send-proposal must set awaiting_approval")
        with s.check("proposal re-send is an idempotent no-op"):
            again_prop = delivery_actions.action_send_proposal(SLUG, config_path)
            expect(again_prop["ok"] and again_prop.get("already_sent"), "second proposal send not a no-op")

        # ---- 6. gate: approve spawns the fulfillment chain ---- #
        approved = {}
        with s.check("approve spawns ordered fulfillment chain with model routing"):
            approved = proposal_actions.action_approve(f"synth:{SLUG}", str(config_path))
            expect(approved["ok"], f"approve failed: {approved}")
            chain = approved["chain"]
            expect([c["title"].split(":")[0] for c in chain] == ["do_build", "review", "final_delivery"],
                   "fulfillment order wrong")
            first = task_rows(db_path, "SELECT status FROM tasks WHERE id = ?", chain[0]["task_id"])[0]
            expect(first["status"] == "ready", "first fulfillment card must land ready")
            review = task_rows(db_path, "SELECT model_override FROM tasks WHERE id = ?", chain[1]["task_id"])[0]
            expect(review["model_override"] == "frontier-model-x", "stage-level model override missing")
            final = task_rows(db_path, "SELECT body FROM tasks WHERE id = ?", chain[2]["task_id"])[0]
            expect(f"delivery_actions.py" in final["body"] and f"deliver {SLUG}" in final["body"],
                   "final stage lacks delivery instruction")

        # ---- 7. delivery hook ---- #
        with s.check("delivery: blocks honestly when the deliverable is missing"):
            try:
                delivery_actions.action_deliver(SLUG, config_path, dry_run=True)
                expect(False, "missing deliverable did not raise")
            except FileNotFoundError:
                pass
        with s.check("delivery: resolves, sends via hermes, records delivery"):
            workspace = root / "work" / "builds" / SLUG
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "deliverable.md").write_text("# The deliverable\n", encoding="utf-8")
            sent = {}
            with (
                patch.object(delivery_actions.shutil, "which", return_value="hermes"),
                patch.object(delivery_actions.subprocess, "run",
                             return_value=Mock(returncode=0, stdout="", stderr="")) as run_cmd,
            ):
                sent = delivery_actions.action_deliver(SLUG, config_path)
            expect(sent["ok"], f"delivery failed: {sent}")
            command = run_cmd.call_args.args[0]
            expect(command[1] == "send" and "discord:synthetic-gate" in command, f"bad send command: {command}")
            expect("MEDIA:" in command[-1], "deliverable not sent as a native attachment")
            expect(vault.load(SLUG).frontmatter["status"] == "delivered", "item not marked delivered")
        with s.check("delivery re-run is an idempotent no-op"):
            again = delivery_actions.action_deliver(SLUG, config_path)
            expect(again["ok"] and again.get("already_delivered"), "second delivery not a no-op")

    passed = sum(1 for r in s.results if r["ok"])
    summary = {
        "ok": passed == len(s.results),
        "passed": passed,
        "total": len(s.results),
        "results": s.results,
    }
    out = report_path or (ROOT / "work" / "eval" / "synthetic-report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    summary["report_path"] = str(out)
    tmp.cleanup()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    summary = run(Path(args.report) if args.report else None)
    for result in summary["results"]:
        mark = "PASS" if result["ok"] else "FAIL"
        line = f"[{mark}] {result['scenario']}"
        if result["detail"]:
            line += f" — {result['detail']}"
        print(line)
    print(f"\n{summary['passed']}/{summary['total']} scenarios passed. "
          f"Report: {summary['report_path']}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
