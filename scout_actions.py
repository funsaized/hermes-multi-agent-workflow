#!/usr/bin/env python3
"""Validate and submit a scout report to its config-scoped intake directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

from engine.config import TriageConfig
from engine.intake_parser import parse_intake_report


def submit_report(config: TriageConfig, source_id: str, draft_path: str | Path) -> dict:
    source = next((candidate for candidate in config.sources if candidate.id == source_id), None)
    if source is None:
        raise ValueError(f"Unknown source {source_id!r}; expected one of {[s.id for s in config.sources]}.")

    draft = Path(draft_path).resolve()
    project_root = Path(config.hermes.project_root).resolve()
    if draft != project_root and project_root not in draft.parents:
        raise ValueError("Draft report must be inside the configured project workspace.")
    text = draft.read_text(encoding="utf-8")
    report = parse_intake_report(text)
    if report.metadata.get("source") != source_id:
        raise ValueError(f"Report source must be {source_id!r}.")
    try:
        captured = datetime.fromisoformat(report.metadata["captured_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise ValueError("Report captured_at must be an ISO-8601 UTC timestamp.") from exc
    if captured.utcoffset() != timezone.utc.utcoffset(captured):
        raise ValueError("Report captured_at must use UTC (Z or +00:00).")
    if not report.candidates:
        raise ValueError("Report must contain at least one candidate.")
    for candidate in report.candidates:
        if not candidate.title or not candidate.claim or not candidate.sources:
            raise ValueError("Every candidate needs a title, claim, and source.")
        if any(not source_ref.get("url", "").startswith(("https://", "http://")) for source_ref in candidate.sources):
            raise ValueError(f"Candidate {candidate.title!r} has a missing or invalid source URL.")

    intake_dir = (config.workspace_path / "vault" / "intake").resolve()
    intake_dir.mkdir(parents=True, exist_ok=True)
    stamp = captured.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    destination = (intake_dir / f"{stamp}-{source_id}.md").resolve()
    if intake_dir not in destination.parents:
        raise ValueError("Resolved report path escapes the configured intake directory.")

    created = not destination.exists()
    if created:
        destination.write_text(text, encoding="utf-8")
    elif destination.read_text(encoding="utf-8") != text:
        raise FileExistsError(f"A different report already exists at {destination}.")

    executable = shutil.which("hermes")
    if not executable:
        if created:
            destination.unlink(missing_ok=True)
        raise RuntimeError("Hermes executable is unavailable.")
    title_stamp = captured.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    command = [
        executable, "kanban", "--board", config.board, "create",
        f"intake: {source_id} {title_stamp}",
        "--body", str(destination),
        "--assignee", config.role_to_profile("orchestrator"),
        "--workspace", f"dir:{project_root}",
        "--created-by", source.profile,
        "--skill", config.orchestrator_skill,
        "--idempotency-key", f"{config.pipeline_id}:intake:{source_id}:{title_stamp}",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    task_id = payload.get("id") or (payload.get("task") or {}).get("id")
    if result.returncode or not task_id:
        if created:
            destination.unlink(missing_ok=True)
        detail = result.stderr.strip() or result.stdout.strip() or "missing task id"
        raise RuntimeError(f"Hermes intake creation failed: {detail}")
    return {"ok": True, "pipeline_id": config.pipeline_id, "source": source_id, "report": str(destination), "task_id": task_id}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="triage.yaml")
    parser.add_argument("source")
    parser.add_argument("--report-file", required=True)
    args = parser.parse_args()
    print(json.dumps(submit_report(TriageConfig.load(args.config), args.source, args.report_file), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
