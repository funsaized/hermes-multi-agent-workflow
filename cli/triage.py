#!/usr/bin/env python3
"""Triage engine CLI:  python -m cli.triage <command>

Commands:
  validate   Load triage.yaml and check it for consistency. FULLY IMPLEMENTED —
             run this after every edit.
  scaffold   Render a structured, side-effect-free Hermes deployment plan as
             safely quoted shell or JSON. It never executes the commands.
  preflight  Run structured, read-only Hermes capability and deployment checks.
  render-skills
             Materialize profile-specific skills under work/scaffold without
             writing to live Hermes profiles.
  init       Stub. Intended to copy triage.yaml + path templates into a fresh
             project. For now, copy this repo and edit triage.yaml directly.
  install    Stub. Intended to actually run the scaffold plan. Left manual on
             purpose — review the printed commands and run them yourself.

This is a TEMPLATE. `scaffold`/`init`/`install` are intentionally conservative —
they tell you what to do rather than mutate your Hermes install behind your back.
Wire them up to your environment as you adopt the template.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine.config import ConfigError, TriageConfig
from engine.hermes_preflight import run_preflight
from engine.scaffold import build_deployment_plan, render_json, render_shell
from engine.skill_materialization import SkillTemplateError, materialize_skills


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = TriageConfig.load(args.config)
    except ConfigError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[OK] triage.yaml valid - pipeline {cfg.name!r} ({cfg.pipeline_id})")
    print(f"  board: {cfg.board}   workspace_root: {cfg.workspace_root}   cost_gate: ${cfg.cost_gate_usd}")
    print(f"  sources: {[s.id for s in cfg.sources]}")
    print(f"  rubric: {len(cfg.rubric.dimensions)} dims, threshold {cfg.rubric.threshold}/{cfg.rubric.max_total}")
    print(f"  research lanes: {cfg.research.lanes} (classifier: {cfg.research.classifier_lane})")
    print(f"  routes: {cfg.route.map}")
    print(f"  paths: {sorted(cfg.paths)}")
    print(f"  roles -> profiles: {cfg.roles}")
    for warning in cfg.validation_warnings:
        print(f"  ! {warning}")
    # Warn about referenced-but-missing template files (non-fatal).
    missing = []
    def exists(rel: str) -> bool:
        path = Path(rel)
        return (path if path.is_absolute() else Path(cfg.hermes.project_root) / path).exists()

    if cfg.research.guide and not exists(cfg.research.guide):
        missing.append(cfg.research.guide)
    for p in cfg.paths.values():
        for rel in (p.scope_rails, p.deliverable_spec, p.proposal_template):
            if rel and not exists(rel):
                missing.append(rel)
    if missing:
        print("  ! referenced template files not found (relative to CWD; fill them in or run from repo root):")
        for m in sorted(set(missing)):
            print(f"      - {m}")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    try:
        cfg = TriageConfig.load(args.config)
    except ConfigError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    if not args.no_preflight:
        report = run_preflight(cfg)
        if not report.ok:
            print(
                f"[preflight warning] {len(report.errors)} blocker(s) detected; "
                "the scaffold below is a read-only plan. Run `python -m cli.triage preflight` for evidence.",
                file=sys.stderr,
            )
        for warning in report.warnings:
            print(f"[preflight warning] {warning}", file=sys.stderr)
    plan = build_deployment_plan(cfg)
    output = render_json(plan) if args.format == "json" else render_shell(plan)
    print(output, end="")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    try:
        cfg = TriageConfig.load(args.config)
    except ConfigError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    report = run_preflight(cfg)
    print(report.render_json() if args.format == "json" else report.render_text(), end="")
    return 0 if report.ok else 1


def cmd_render_skills(args: argparse.Namespace) -> int:
    try:
        cfg = TriageConfig.load(args.config)
        targets = materialize_skills(cfg)
    except (ConfigError, SkillTemplateError, OSError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    for target in targets:
        print(f"[rendered] {target.path}")
        print(f"  reviewed manual destination: {target.live_destination}")
    print("No live Hermes profile was modified; installation remains a reviewed manual step.")
    return 0


def cmd_stub(name: str):
    def run(args: argparse.Namespace) -> int:
        print(f"`{name}` is a stub in this template. See `python -m cli.triage scaffold` for the plan, "
              f"and docs/04-adapting-to-your-domain.md / docs/07-runbook.md. Wire it to your environment.")
        return 0
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli.triage", description="Hermes Multi-Agent Workflow CLI.")
    parser.add_argument("--config", default="triage.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate triage.yaml.").set_defaults(func=cmd_validate)
    scaffold = sub.add_parser("scaffold", help="Print the setup plan from triage.yaml.")
    scaffold.add_argument("--format", choices=("shell", "json"), default="shell")
    scaffold.add_argument(
        "--no-preflight",
        action="store_true",
        help="Render the pure deployment plan without inspecting a live Hermes runtime.",
    )
    scaffold.set_defaults(func=cmd_scaffold)
    preflight = sub.add_parser("preflight", help="Read-only Hermes capability and deployment checks.")
    preflight.add_argument("--format", choices=("text", "json"), default="text")
    preflight.set_defaults(func=cmd_preflight)
    sub.add_parser("render-skills", help="Render profile-specific skills into work/scaffold.").set_defaults(func=cmd_render_skills)
    sub.add_parser("init", help="(stub) Start a new project.").set_defaults(func=cmd_stub("init"))
    sub.add_parser("install", help="(stub) Execute the scaffold plan.").set_defaults(func=cmd_stub("install"))
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
