from __future__ import annotations

import unittest

from engine.config import TriageConfig
from engine.hermes_preflight import PreflightCheck, ToolsetInventory
from scripts.rehearse_scaffold import classify_runtime_toolset_failures
from tests.test_scaffold import ROOT, config_data


class RuntimeToolsetClassifierTests(unittest.TestCase):
    def setUp(self):
        self.cfg = TriageConfig.from_dict(config_data(), config_path=ROOT / "triage.yaml")

    def test_absent_runtime_name_is_skippable(self):
        failures = [
            PreflightCheck("capability.toolset-names.cron", False, "error", "kanban unavailable"),
            PreflightCheck("resource.toolsets.scout.cron", False, "error", "kanban missing"),
        ]
        available = frozenset({"web", "file"})
        inventories = {
            ("default", "cron"): ToolsetInventory(True, available, available),
            ("scout", "cron"): ToolsetInventory(True, available, available),
        }

        skippable, product = classify_runtime_toolset_failures(
            self.cfg, failures, inventories
        )

        self.assertEqual(skippable, failures)
        self.assertEqual(product, [])

    def test_missing_but_available_toolset_is_a_product_failure(self):
        failure = PreflightCheck(
            "resource.toolsets.scout.cron", False, "error", "kanban missing"
        )
        available = frozenset({"web", "file", "kanban"})
        inventories = {
            ("scout", "cron"): ToolsetInventory(
                True,
                available,
                frozenset({"web", "file"}),
            ),
        }

        skippable, product = classify_runtime_toolset_failures(
            self.cfg, [failure], inventories
        )

        self.assertEqual(skippable, [])
        self.assertEqual(product, [failure])

    def test_uninspectable_availability_never_skips(self):
        failure = PreflightCheck(
            "capability.toolset-names.cli", False, "error", "inspection failed"
        )
        inventories = {
            ("default", "cli"): ToolsetInventory(False, frozenset(), frozenset()),
        }

        skippable, product = classify_runtime_toolset_failures(
            self.cfg, [failure], inventories
        )

        self.assertEqual(skippable, [])
        self.assertEqual(product, [failure])


if __name__ == "__main__":
    unittest.main()
