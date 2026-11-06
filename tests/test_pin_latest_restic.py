import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.pin_latest_restic import (
    parse_sha256sums,
    render_manifest,
    select_platform_assets,
)


LATEST_RELEASE = {
    "tag_name": "v0.19.2",
    "assets": [
        {
            "name": "SHA256SUMS",
            "browser_download_url": (
                "https://github.com/restic/restic/releases/download/v0.19.2/SHA256SUMS"
            ),
        },
        {
            "name": "restic_0.19.2_linux_amd64.bz2",
            "browser_download_url": (
                "https://github.com/restic/restic/releases/download/v0.19.2/"
                "restic_0.19.2_linux_amd64.bz2"
            ),
        },
        {
            "name": "restic_0.19.2_windows_amd64.zip",
            "browser_download_url": (
                "https://github.com/restic/restic/releases/download/v0.19.2/"
                "restic_0.19.2_windows_amd64.zip"
            ),
        },
        {
            "name": "restic_0.19.2_windows_amd64.zip.asc",
            "browser_download_url": (
                "https://github.com/restic/restic/releases/download/v0.19.2/"
                "restic_0.19.2_windows_amd64.zip.asc"
            ),
        },
    ],
}

LINUX_DIGEST = "a" * 64
WINDOWS_DIGEST = "b" * 64

SUMS_TEXT = "{}  restic_0.19.2_linux_amd64.bz2\n{}  restic_0.19.2_windows_amd64.zip\n".format(
    LINUX_DIGEST, WINDOWS_DIGEST
)


class ParseSha256SumsTests(unittest.TestCase):
    def test_parses_binary_and_text_variants(self):
        text = (
            "{}  restic_0.19.2_linux_amd64.bz2\n".format(LINUX_DIGEST)
            + "{} *restic_0.19.2_windows_amd64.zip\n".format(WINDOWS_DIGEST)
            + "not-a-digest  ignored\n"
        )

        digests = parse_sha256sums(text)

        self.assertEqual(
            digests,
            {
                "restic_0.19.2_linux_amd64.bz2": LINUX_DIGEST,
                "restic_0.19.2_windows_amd64.zip": WINDOWS_DIGEST,
            },
        )


class SelectPlatformAssetsTests(unittest.TestCase):
    def test_selects_both_platform_binaries_ignoring_signature(self):
        assets = select_platform_assets(LATEST_RELEASE)

        self.assertEqual(
            [asset["name"] for asset in assets],
            [
                "restic_0.19.2_linux_amd64.bz2",
                "restic_0.19.2_windows_amd64.zip",
            ],
        )

    def test_missing_platform_asset_raises(self):
        broken = {
            "assets": [
                {"name": "restic_0.19.2_windows_amd64.zip"},
            ]
        }

        with self.assertRaises(ValueError):
            select_platform_assets(broken)


class RenderManifestTests(unittest.TestCase):
    def test_rendered_manifest_matches_bundle_format(self):
        assets = select_platform_assets(LATEST_RELEASE)

        rendered = render_manifest(
            "v0.19.2", assets, parse_sha256sums(SUMS_TEXT)
        )

        self.assertIn("version: v0.19.2", rendered)
        self.assertIn("  - name: restic_0.19.2_linux_amd64.bz2", rendered)
        self.assertIn("    url: https://github.com/restic/restic/releases/download/v0.19.2/restic_0.19.2_linux_amd64.bz2", rendered)
        self.assertIn("    sha256: " + LINUX_DIGEST, rendered)
        self.assertIn("    sha256: " + WINDOWS_DIGEST, rendered)
        self.assertTrue(rendered.endswith("\n"))

    def test_render_fails_when_digest_missing(self):
        assets = select_platform_assets(LATEST_RELEASE)

        with self.assertRaises(ValueError):
            render_manifest("v0.19.2", assets, {})


if __name__ == "__main__":
    unittest.main()
