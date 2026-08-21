"""Frontmatter round-trip regressions — quoted scalars must invert exactly."""
from __future__ import annotations

import unittest

from engine.frontmatter import dumps_frontmatter, parse_frontmatter


def roundtrip(fm: dict) -> dict:
    parsed, _ = parse_frontmatter(dumps_frontmatter(fm, "body\n"))
    return parsed


class FrontmatterRoundTripTests(unittest.TestCase):
    def test_windows_path_survives_roundtrip(self):
        # Paths contain ":" (drive letter), so they serialize via repr(); the
        # parser must unescape the doubled backslashes on the way back in.
        path = r"C:\Users\someone\work\proposals\widget.md"
        self.assertEqual(roundtrip({"proposal_file": path})["proposal_file"], path)

    def test_colon_and_quote_strings_survive(self):
        for value in ["status: odd", "don't stop", 'say "hi"', "a\\b"]:
            self.assertEqual(roundtrip({"k": value})["k"], value, value)

    def test_plain_scalars_unchanged(self):
        fm = {"n": 3, "f": 1.5, "t": True, "none": None, "s": "plain"}
        self.assertEqual(roundtrip(fm), fm)


if __name__ == "__main__":
    unittest.main()
