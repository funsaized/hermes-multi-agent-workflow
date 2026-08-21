import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scout_actions


REPORT = """source: web
captured_at: 2026-08-21T10:00:00Z

## Candidate: Useful graph
Claim: A concrete claim.
Sources:
  - url: https://example.com/source
    quote: "evidence"
Why it may matter: Useful to engineers.
"""


class ScoutActionsTests(unittest.TestCase):
    def test_writes_only_to_configured_intake_and_creates_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.md"
            draft.write_text(REPORT, encoding="utf-8")
            config = Mock(
                sources=[SimpleNamespace(id="web", profile="scout")],
                workspace_path=root / "graph-output",
                board="graph-board",
                pipeline_id="graph",
                orchestrator_skill="triage-graph",
            )
            config.hermes.project_root = str(root)
            config.role_to_profile.return_value = "default"
            completed = SimpleNamespace(returncode=0, stdout='{"id":"t_intake"}', stderr="")

            with patch.object(scout_actions.shutil, "which", return_value="hermes"), patch.object(
                scout_actions.subprocess, "run", return_value=completed
            ) as run:
                result = scout_actions.submit_report(config, "web", draft)

            expected = root / "graph-output" / "vault" / "intake" / "2026-08-21T10-00-00Z-web.md"
            self.assertEqual(Path(result["report"]), expected)
            self.assertEqual(expected.read_text(encoding="utf-8"), REPORT)
            self.assertIn(f"dir:{root.resolve()}", run.call_args.args[0])
            self.assertEqual(result["task_id"], "t_intake")

    def test_rejects_source_mismatch_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.md"
            draft.write_text(REPORT, encoding="utf-8")
            config = Mock(sources=[SimpleNamespace(id="x", profile="scout")], workspace_path=root / "graph-output")
            config.hermes.project_root = str(root)
            with self.assertRaisesRegex(ValueError, "Report source"):
                scout_actions.submit_report(config, "x", draft)
            self.assertFalse((root / "graph-output").exists())


if __name__ == "__main__":
    unittest.main()
