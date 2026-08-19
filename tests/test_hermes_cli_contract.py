from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from engine.config import TriageConfig
from engine.scaffold import CommandStep, build_deployment_plan


LONG_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def command_contracts(cfg: TriageConfig) -> dict[tuple[str, ...], set[str]]:
    """Reduce the generated plan to unique help paths and long options."""
    contracts: dict[tuple[str, ...], set[str]] = {}
    for step in build_deployment_plan(cfg).steps:
        if not isinstance(step, CommandStep):
            continue
        argv = list(step.argv[1:])
        if argv[:1] == ["-p"]:
            argv = argv[2:]
        command: list[str] = []
        for token in argv:
            if token.startswith("-") or command and token not in {
                "boards", "create", "switch", "set", "enable", "install"
            }:
                break
            command.append(token)
        flags = {token for token in argv if token.startswith("--")}
        contracts.setdefault(tuple(command), set()).update(flags)
    return contracts


class PlannerContractExtractionTests(unittest.TestCase):
    def test_extracts_every_generated_command_surface(self):
        contracts = command_contracts(TriageConfig.load())
        self.assertEqual(
            set(contracts),
            {
                ("kanban", "boards", "create"),
                ("kanban", "boards", "switch"),
                ("profile", "create"),
                ("config", "set"),
                ("tools", "enable"),
                ("cron", "create"),
                ("gateway", "install"),
            },
        )


@unittest.skipUnless(shutil.which("hermes"), "Hermes executable unavailable; live CLI contract skipped")
@unittest.skipUnless(
    os.environ.get("HERMES_RUN_CLI_CONTRACT") == "1",
    "set HERMES_RUN_CLI_CONTRACT=1 to check the installed Hermes help surface",
)
class LiveHermesCliContractTests(unittest.TestCase):
    def test_generated_commands_and_flags_exist_on_installed_help_surface(self):
        cfg = TriageConfig.load()
        env = {**os.environ, "NO_COLOR": "1"}
        for command, expected_flags in command_contracts(cfg).items():
            with self.subTest(command=" ".join(command)):
                profile_prefix = ("-p", cfg.hermes.base_profile) if command[0] in {"config", "tools", "cron", "gateway"} else ()
                result = subprocess.run(
                    ("hermes", *profile_prefix, *command, "--help"),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                capabilities = set(LONG_FLAG.findall(result.stdout + result.stderr))
                self.assertLessEqual(expected_flags, capabilities)


if __name__ == "__main__":
    unittest.main()
