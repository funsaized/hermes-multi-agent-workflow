"""delivery_actions adapter tests — resolution rules, guards, and the send path."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import delivery_actions
from engine.config import TriageConfig
from engine.item_vault import ItemVault
from tests.pipeline_fixtures import make_pipeline


class DeliveryActionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="delivery actions ")
        self.root = Path(self._tmp.name)
        self.config_path, self.db_path = make_pipeline(self.root)
        self.config = TriageConfig.load(self.config_path)
        self._env = patch.dict(os.environ, {"HERMES_KANBAN_DB": str(self.db_path)})
        self._env.start()
        os.environ.pop("TRIAGE_VAULT_DIR", None)

        self.vault = ItemVault(self.root / "work" / "vault" / "items")
        item = self.vault.create_item(
            slug="widget", title="Widget", sources=[{"url": "https://example.com"}], body="body"
        )
        item.frontmatter["status"] = "approved"
        item.frontmatter["path"] = "build"
        self.vault.save(item)
        self.workspace = self.root / "work" / "builds" / "widget"
        self.workspace.mkdir(parents=True)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_dry_run_resolves_configured_deliverable(self):
        (self.workspace / "deliverable.md").write_text("done", encoding="utf-8")
        result = delivery_actions.action_deliver("widget", self.config_path, dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(Path(result["deliverable"]).name, "deliverable.md")
        self.assertEqual(result["target"], "discord:synthetic-gate")
        self.assertIn("send", result["command"])
        # One message: caption + MEDIA attachment tag, never file-as-text.
        self.assertIn("MEDIA:", result["command"][-1])
        self.assertNotIn("--file", result["command"])
        self.assertLessEqual(len(result["caption"]), delivery_actions.GATE_CAPTION_LIMIT)
        # Dry-run mutates nothing.
        self.assertEqual(self.vault.load("widget").frontmatter["status"], "approved")

    def test_missing_deliverable_fails_loudly_with_listing(self):
        (self.workspace / "notes.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(FileNotFoundError) as ctx:
            delivery_actions.action_deliver("widget", self.config_path, dry_run=True)
        self.assertIn("notes.txt", str(ctx.exception))

    def test_explicit_file_outside_workspace_is_rejected(self):
        outside = self.root / "outside.md"
        outside.write_text("nope", encoding="utf-8")
        with self.assertRaises(ValueError):
            delivery_actions.resolve_deliverable(self.workspace.resolve(), None, str(outside))

    def test_ambiguous_fallback_raises(self):
        ws = self.workspace.resolve()
        (ws / "deliverable.md").write_text("a", encoding="utf-8")
        (ws / "deliverable.zip").write_text("b", encoding="utf-8")
        with self.assertRaises(ValueError):
            delivery_actions.resolve_deliverable(ws, None, None)

    def test_requires_approved_status(self):
        item = self.vault.load("widget")
        item.frontmatter["status"] = "triage"
        self.vault.save(item)
        (self.workspace / "deliverable.md").write_text("done", encoding="utf-8")
        with self.assertRaises(SystemExit):
            delivery_actions.action_deliver("widget", self.config_path, dry_run=True)

    def test_already_delivered_is_idempotent_noop(self):
        item = self.vault.load("widget")
        item.frontmatter["status"] = "delivered"
        item.frontmatter["delivered_at"] = "2026-08-21T00:00:00Z"
        self.vault.save(item)
        result = delivery_actions.action_deliver("widget", self.config_path)
        self.assertTrue(result["ok"])
        self.assertTrue(result["already_delivered"])

    def test_successful_send_records_delivery_and_comments(self):
        (self.workspace / "deliverable.md").write_text("done", encoding="utf-8")
        item = self.vault.load("widget")
        item.frontmatter["linked_kanban_tasks"] = ["t_root1"]
        self.vault.save(item)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, priority, created_by,"
            " created_at, workspace_kind, consecutive_failures)"
            " VALUES ('t_root1', 'triage: widget', 'b', 'orchestrator', 'done', 0, 'x', 0, 'scratch', 0)"
        )
        conn.commit()
        conn.close()

        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run",
                         return_value=Mock(returncode=0, stdout="", stderr="")) as run,
        ):
            result = delivery_actions.action_deliver("synth:widget", self.config_path, task_id="t_final")
        self.assertTrue(result["ok"])
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["hermes", "send", "--to"])
        self.assertEqual(command[3], "discord:synthetic-gate")
        self.assertIn("MEDIA:", command[4])
        self.assertEqual(result["send_attempts"], 1)
        fm = self.vault.load("widget").frontmatter
        self.assertEqual(fm["status"], "delivered")
        self.assertIn("delivered_at", fm)
        conn = sqlite3.connect(str(self.db_path))
        comments = conn.execute("SELECT task_id FROM task_comments").fetchall()
        conn.close()
        self.assertEqual({c[0] for c in comments}, {"t_root1", "t_final"})

    def test_failed_send_exits_backend_and_keeps_item_approved(self):
        (self.workspace / "deliverable.md").write_text("done", encoding="utf-8")
        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run",
                         return_value=Mock(returncode=1, stdout="", stderr="channel down")),
            patch.object(delivery_actions.time, "sleep") as sleep,
            self.assertRaises(SystemExit),
        ):
            delivery_actions.action_deliver("widget", self.config_path)
        self.assertEqual(self.vault.load("widget").frontmatter["status"], "approved")
        sleep.assert_not_called()  # non-429 failures never retry

    def test_rate_limited_send_backs_off_then_succeeds(self):
        (self.workspace / "deliverable.md").write_text("done", encoding="utf-8")
        responses = [
            Mock(returncode=1, stdout="", stderr="Discord API error (429): retry_after 0.5s"),
            Mock(returncode=1, stdout="", stderr="rate limit reached"),
            Mock(returncode=0, stdout="", stderr=""),
        ]
        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run", side_effect=responses),
            patch.object(delivery_actions.time, "sleep") as sleep,
        ):
            result = delivery_actions.action_deliver("widget", self.config_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["send_attempts"], 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],
                         list(delivery_actions.RATE_LIMIT_RETRY_DELAYS[:2]))

    def test_rate_limited_send_gives_up_after_bounded_retries(self):
        (self.workspace / "deliverable.md").write_text("done", encoding="utf-8")
        limited = Mock(returncode=1, stdout="", stderr="429 Too Many Requests")
        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run", return_value=limited) as run,
            patch.object(delivery_actions.time, "sleep") as sleep,
            self.assertRaises(SystemExit),
        ):
            delivery_actions.action_deliver("widget", self.config_path)
        self.assertEqual(run.call_count, len(delivery_actions.RATE_LIMIT_RETRY_DELAYS) + 1)
        self.assertEqual(sleep.call_count, len(delivery_actions.RATE_LIMIT_RETRY_DELAYS))
        self.assertEqual(self.vault.load("widget").frontmatter["status"], "approved")

    def test_legacy_cli_form_still_means_deliver(self):
        self.assertEqual(
            delivery_actions._normalize_argv(["--config", "x.yaml", "widget", "--dry-run"]),
            ["--config", "x.yaml", "deliver", "widget", "--dry-run"],
        )
        self.assertEqual(
            delivery_actions._normalize_argv(["send-proposal", "widget"]),
            ["send-proposal", "widget"],
        )
        self.assertEqual(
            delivery_actions._normalize_argv(["deliver", "widget"]),
            ["deliver", "widget"],
        )


class SendProposalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="send proposal ")
        self.root = Path(self._tmp.name)
        self.config_path, self.db_path = make_pipeline(self.root)
        self.config = TriageConfig.load(self.config_path)
        self._env = patch.dict(os.environ, {"HERMES_KANBAN_DB": str(self.db_path)})
        self._env.start()
        os.environ.pop("TRIAGE_VAULT_DIR", None)

        self.vault = ItemVault(self.root / "work" / "vault" / "items")
        item = self.vault.create_item(
            slug="widget", title="Widget", sources=[{"url": "https://example.com"}], body="body"
        )
        item.frontmatter["path"] = "build"
        item.frontmatter["score"] = 12
        self.vault.save(item)
        self.proposal = self.config.workspace_path / "proposals" / "widget.md"
        self.proposal.parent.mkdir(parents=True, exist_ok=True)
        # A proposal far beyond any single-message text limit.
        self.proposal.write_text("# Proposal\n" + ("x" * 17000), encoding="utf-8")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_dry_run_builds_single_attachment_message(self):
        result = delivery_actions.action_send_proposal("widget", self.config_path, dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        message = result["command"][-1]
        self.assertIn("MEDIA:", message)
        self.assertIn(str(self.proposal.resolve()), message)
        # Everything needed to act from the caption alone, within the single-message cap.
        self.assertIn("approve synth:widget", result["caption"])
        self.assertIn("shelve synth:widget", result["caption"])
        self.assertLessEqual(len(result["caption"]), delivery_actions.GATE_CAPTION_LIMIT)
        # Dry-run mutates nothing.
        self.assertEqual(self.vault.load("widget").frontmatter["status"], "triage")

    def test_successful_send_sets_awaiting_approval_and_records(self):
        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run",
                         return_value=Mock(returncode=0, stdout="", stderr="")) as run,
        ):
            result = delivery_actions.action_send_proposal("synth:widget", self.config_path)
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_count, 1)
        fm = self.vault.load("widget").frontmatter
        self.assertEqual(fm["status"], "awaiting_approval")
        self.assertIn("proposal_sent_at", fm)
        self.assertEqual(fm["proposal_file"], str(self.proposal.resolve()))

    def test_resend_is_idempotent_unless_forced(self):
        item = self.vault.load("widget")
        item.frontmatter["status"] = "awaiting_approval"
        item.frontmatter["proposal_sent_at"] = "2026-08-21T00:00:00Z"
        self.vault.save(item)
        result = delivery_actions.action_send_proposal("widget", self.config_path)
        self.assertTrue(result["already_sent"])
        # --resend goes through the full send again (redraft flow).
        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run",
                         return_value=Mock(returncode=0, stdout="", stderr="")) as run,
        ):
            forced = delivery_actions.action_send_proposal("widget", self.config_path, resend=True)
        self.assertTrue(forced["ok"])
        self.assertNotIn("already_sent", forced)
        self.assertEqual(run.call_count, 1)

    def test_decided_items_refuse_proposal_send(self):
        for status in ["approved", "delivered", "shelved_by_human", "auto_shelve"]:
            item = self.vault.load("widget")
            item.frontmatter["status"] = status
            self.vault.save(item)
            with self.assertRaises(SystemExit):
                delivery_actions.action_send_proposal("widget", self.config_path, dry_run=True)

    def test_missing_proposal_file_fails_with_expected_path(self):
        self.proposal.unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            delivery_actions.action_send_proposal("widget", self.config_path, dry_run=True)
        self.assertIn("proposals", str(ctx.exception))

    def test_failed_send_keeps_item_status_untouched(self):
        with (
            patch.object(delivery_actions.shutil, "which", return_value="hermes"),
            patch.object(delivery_actions.subprocess, "run",
                         return_value=Mock(returncode=1, stdout="", stderr="channel down")),
            patch.object(delivery_actions.time, "sleep"),
            self.assertRaises(SystemExit),
        ):
            delivery_actions.action_send_proposal("widget", self.config_path)
        self.assertEqual(self.vault.load("widget").frontmatter["status"], "triage")


if __name__ == "__main__":
    unittest.main()
