from __future__ import annotations

import os
import shutil
import unittest

from scripts.rehearse_scaffold import run_rehearsal


@unittest.skipUnless(
    os.environ.get("HERMES_RUN_DISPOSABLE_REHEARSAL") == "1",
    "set HERMES_RUN_DISPOSABLE_REHEARSAL=1 to mutate an automatically cleaned temporary Hermes home",
)
@unittest.skipUnless(shutil.which("hermes"), "Hermes executable unavailable; disposable-home rehearsal skipped")
class DisposableHomeRehearsalTests(unittest.TestCase):
    def test_generated_topology_in_disposable_home(self):
        report = run_rehearsal()
        if report.runtime_compatibility_blocked:
            self.skipTest("Disposable topology was safe but runtime compatibility blocked readiness: " + " ".join(report.evidence))
        self.assertTrue(report.ok, " ".join(report.evidence))


if __name__ == "__main__":
    unittest.main()
