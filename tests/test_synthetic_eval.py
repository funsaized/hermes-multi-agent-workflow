"""Runs the synthetic end-to-end eval as part of the unit suite.

The eval itself lives in scripts/run_synthetic_eval.py (also runnable
standalone); this wrapper keeps `python -m unittest discover -s tests` as the
single command that proves the pipeline's deterministic spine end to end.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_synthetic_eval import run  # noqa: E402


class SyntheticEvalTests(unittest.TestCase):
    def test_full_synthetic_cycle_passes(self):
        with tempfile.TemporaryDirectory(prefix="eval report ") as tmp:
            summary = run(Path(tmp) / "report.json")
        failures = [r for r in summary["results"] if not r["ok"]]
        self.assertEqual(failures, [], f"synthetic eval failures: {failures}")


if __name__ == "__main__":
    unittest.main()
