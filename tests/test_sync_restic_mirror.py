import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.sync_restic_mirror import (
    append_waf_paths,
    asset_names_for,
    mirror_url,
    parse_sums,
)


def per_path_rule(path, rule_id="rule-seed"):
    return {
        "id": rule_id,
        "action": "skip",
        "description": "mirror allow {}".format(path),
        "enabled": True,
        "expression": (
            '(http.host eq "dl.m2r.pfdr.cc" and http.request.method in {{"GET" "HEAD"}}'
            ' and http.request.uri.path eq "{}")'.format(path)
        ),
    }


COMBINED_RULE = {
    "id": "rule-combined",
    "action": "skip",
    "description": "mirror allow seeded paths",
    "enabled": True,
    "expression": (
        'http.host eq "dl.m2r.pfdr.cc" and http.request.method in {"GET" "HEAD"} and '
        'http.request.uri.path in {"/v0.19.1/SHA256SUMS" "/v0.19.1/restic_0.19.1_linux_amd64.bz2"}'
    ),
}

NEW_PATHS = [
    "/v0.19.2/SHA256SUMS",
    "/v0.19.2/restic_0.19.2_linux_amd64.bz2",
    "/v0.19.2/restic_0.19.2_windows_amd64.zip",
]


class AssetNamingTests(unittest.TestCase):
    def test_asset_names_cover_platforms_and_sums(self):
        self.assertEqual(
            asset_names_for("v0.19.2"),
            [
                "restic_0.19.2_linux_amd64.bz2",
                "restic_0.19.2_windows_amd64.zip",
                "SHA256SUMS",
            ],
        )

    def test_mirror_url_keeps_version_directory(self):
        self.assertEqual(
            mirror_url("v0.19.1", "SHA256SUMS"),
            "https://dl.m2r.pfdr.cc/v0.19.1/SHA256SUMS",
        )


class ParseSumsTests(unittest.TestCase):
    def test_parses_official_sums_layout(self):
        text = "{}  restic_0.19.2_linux_amd64.bz2\n{} *restic_0.19.2_windows_amd64.zip\n".format(
            "a" * 64, "b" * 64
        )

        self.assertEqual(
            parse_sums(text),
            {
                "restic_0.19.2_linux_amd64.bz2": "a" * 64,
                "restic_0.19.2_windows_amd64.zip": "b" * 64,
            },
        )


class AppendWafPathsTests(unittest.TestCase):
    def test_per_path_structure_clones_template_rule(self):
        rules = [
            {"id": "unrelated", "expression": 'http.request.uri.path eq "/other"'},
            per_path_rule("/v0.19.1/SHA256SUMS"),
        ]

        updated, appended = append_waf_paths(rules, NEW_PATHS)

        self.assertEqual(appended, 3)
        self.assertEqual(len(updated), 5)
        cloned = [rule for rule in updated if "v0.19.2" in str(rule.get("expression"))]
        self.assertEqual(len(cloned), 3)
        self.assertTrue(all("id" not in rule for rule in cloned))
        self.assertIn("v0.19.2/restic_0.19.2_windows_amd64.zip", cloned[-1]["expression"])
        self.assertIn("v0.19.2", cloned[-1]["description"])

    def test_combined_structure_appends_into_braces(self):
        rules = [dict(COMBINED_RULE)]

        updated, appended = append_waf_paths(rules, NEW_PATHS[:1])

        self.assertEqual(appended, 1)
        self.assertEqual(len(updated), 1)
        self.assertIn(
            '"/v0.19.1/restic_0.19.1_linux_amd64.bz2" "/v0.19.2/SHA256SUMS"',
            updated[0]["expression"],
        )

    def test_existing_paths_are_skipped(self):
        rules = [per_path_rule("/v0.19.1/SHA256SUMS")]

        updated, appended = append_waf_paths(rules, ["/v0.19.1/SHA256SUMS"])

        self.assertEqual(appended, 0)
        self.assertEqual(len(updated), 1)

    def test_unknown_structure_refuses_to_guess(self):
        rules = [{"id": "challenge", "expression": '(cf.client.bot)'}]

        with self.assertRaises(SystemExit):
            append_waf_paths(rules, NEW_PATHS)


if __name__ == "__main__":
    unittest.main()
