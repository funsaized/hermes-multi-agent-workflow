"""Shared synthetic-pipeline fixtures for adapter tests and the synthetic eval.

Builds a complete but domain-neutral pipeline in a temp directory: a validated
triage.yaml, an empty vault, and a board DB using the SAME schema an installed
Hermes 0.20 board exposes (including the optional `idempotency_key`, `skills`,
and model-override columns), so the adapters are exercised against the real
column contract without touching any live board.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

# Mirrors the columns observed on a live Hermes 0.20 board. The adapters only
# REQUIRE the core columns; the optional ones prove the schema-tolerant writes.
BOARD_SCHEMA_SQL = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT,
    status TEXT, priority INTEGER, created_by TEXT, created_at INTEGER,
    started_at INTEGER, completed_at INTEGER,
    workspace_kind TEXT, workspace_path TEXT,
    result TEXT, idempotency_key TEXT, consecutive_failures INTEGER,
    skills TEXT, model_override TEXT, provider_override TEXT, reasoning_effort TEXT
);
CREATE TABLE task_links (parent_id TEXT, child_id TEXT, UNIQUE(parent_id, child_id));
CREATE TABLE task_comments (task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
CREATE TABLE task_events (task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
"""

INTAKE_REPORT = """source: web
captured_at: 2026-08-21T10:00:00Z

## Candidate: Build a widget frobnicator
Claim: Teams cannot frobnicate widgets and it blocked releases.
Sources:
  - url: https://example.com/frob
    quote: "we failed and gave up"
Why it may matter: Widespread pain across teams.

## Candidate: Minor cosmetic nit
Claim: A button is slightly off-center.
Sources:
  - url: https://example.com/nit
    quote: "looks odd"
Why it may matter: Low impact.

## Candidate: Build the widget frobnicator
Claim: Teams cannot frobnicate widgets and it blocked releases.
Sources:
  - url: https://example.com/frob2
    quote: "same failure again"
Why it may matter: Widespread pain across teams.
"""


def pipeline_config_data() -> dict:
    """A synthetic pipeline exercising every mechanism the adapters support."""
    return {
        "name": "synthetic pipeline",
        "pipeline_id": "synth",
        "board": "synth-board",
        "workspace_root": "./work",
        "sources": [
            {
                "id": "web",
                "profile": "scout",
                "skill": "synth-scout",
                "schedule": "15 * * * *",
                "query": "Find concrete reports with sources.",
            }
        ],
        "item_schema": {"fields": ["title", "claim", "sources"]},
        "dedup": {"method": "token-cosine", "duplicate_threshold": 0.62, "possible_threshold": 0.40},
        "rubric": {
            "threshold": 50,
            "dimensions": [
                {"key": "frequency", "max": 25, "hint": "distinct sources"},
                {"key": "intensity", "max": 25, "hint": "strength of language"},
            ],
        },
        "research_lanes": {
            "role": "researcher",
            "lanes": ["verify", "audit", "classify"],
            "classifier_lane": "classify",
        },
        "route": {"classifier": "classify.result", "map": {"missing": "build", "good": "shelve"}},
        "paths": {
            "build": {
                "prep": [{"stage": "synth_prep", "role": "analyst"}],
                "propose": {"role": "orchestrator"},
                "fulfill": [
                    {"stage": "do_build", "role": "builder"},
                    {"stage": "review", "role": "reviewer", "model": "frontier-model-x"},
                    {"stage": "final_delivery", "role": "orchestrator"},
                ],
                "workspace_subdir": "builds",
                "deliverable": "deliverable.md",
            },
            "shelve": {"auto": True},
        },
        "roles": {
            "orchestrator": "orchestrator",
            "researcher": {"profile": "researcher", "model": "cheap-model-1", "reasoning_effort": "low"},
            "analyst": "analyst",
            "builder": {"profile": "builder", "model": "cheap-model-1"},
            "reviewer": "reviewer",
        },
        "gate": {"channel": "discord", "target": "discord:synthetic-gate"},
        "hermes": {
            "min_version": "0.20.0",
            "base_profile": "default",
            "gateway_profile": "orchestrator",
            "project_root": ".",
            "profile_strategy": "clone",
            "max_spawn": 3,
            "profiles": {
                "orchestrator": {"description": "Routes work and handles the gate.", "toolsets": ["file", "terminal"]},
                "researcher": {"description": "Research lanes.", "toolsets": ["web", "file"]},
                "analyst": {"description": "Prep analysis.", "toolsets": ["file"]},
                "builder": {"description": "Builds artifacts.", "toolsets": ["file", "terminal"]},
                "reviewer": {"description": "Verifies artifacts.", "toolsets": ["file", "terminal"]},
                "scout": {"description": "Scheduled scout.", "toolsets": ["web", "file", "terminal"], "owns_cron": True},
            },
        },
    }


def make_pipeline(root: Path, data: dict | None = None) -> tuple[Path, Path]:
    """Write the synthetic config and an empty live-schema board under `root`.

    Returns (config_path, board_db_path). Point HERMES_KANBAN_DB at the DB so
    every adapter resolves it without touching a real Hermes home.
    """
    config_path = root / "triage.yaml"
    config_path.write_text(
        yaml.safe_dump(data or pipeline_config_data(), sort_keys=False), encoding="utf-8"
    )
    db_path = root / "kanban.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(BOARD_SCHEMA_SQL)
    conn.commit()
    conn.close()
    return config_path, db_path


def write_intake_report(
    root: Path, text: str = INTAKE_REPORT, name: str = "2026-08-21T10-00-00Z-web.md"
) -> Path:
    intake_dir = root / "work" / "vault" / "intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    report = intake_dir / name
    report.write_text(text, encoding="utf-8")
    return report


def mark_done(db_path: Path, task_ids: list[str]) -> None:
    """Play the dispatcher: mark tasks done so children promote (eval-only)."""
    conn = sqlite3.connect(str(db_path))
    try:
        for task_id in task_ids:
            conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()
