import bz2
import hashlib
import os
import zipfile

try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        ResticDownloadTarget,
        build_default_config,
        expected_restic_sha256,
        extract_sha256_from_sums,
        fetch_restic_release_metadata,
        get_default_restic_version,
        install_restic_archive,
        install_restic_from_urls,
        mock,
        tempfile,
        unittest,
        verify_restic_download,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakePluginServer,
        ResticDownloadTarget,
        build_default_config,
        expected_restic_sha256,
        extract_sha256_from_sums,
        fetch_restic_release_metadata,
        get_default_restic_version,
        install_restic_archive,
        install_restic_from_urls,
        mock,
        tempfile,
        unittest,
        verify_restic_download,
    )


DOWNLOAD_MODULE = "mcdr2restic.restic.restic_download"

FAKE_BINARY = b"restic-fake-binary"
FAKE_BINARY_SHA256 = hashlib.sha256(FAKE_BINARY).hexdigest()

TRUSTED_TARGET = ResticDownloadTarget(
    asset_url=(
        "https://github.com/restic/restic/releases/download/v0.19.1/"
        "restic_0.19.1_windows_amd64.zip"
    ),
    asset_name="restic_0.19.1_windows_amd64.zip",
    asset_keyword="windows_amd64.zip",
    output_name="restic.exe",
    target_path="restic.exe",
    timeout=10,
    version="v0.19.1",
    sha256=FAKE_BINARY_SHA256,
)


class TrustedReleaseMetadataTests(unittest.TestCase):
    def test_trusted_version_skips_github_api(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".download_bytes", side_effect=AssertionError("no api")
        ):
            release = fetch_restic_release_metadata("v0.19.1", 10)

        self.assertEqual(release.get("tag_name"), "v0.19.1")
        self.assertTrue(
            any(asset.get("sha256") for asset in release.get("assets", []))
        )

    def test_latest_falls_back_to_trusted_release_when_api_fails(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".download_bytes", side_effect=OSError("offline")
        ):
            release = fetch_restic_release_metadata("latest", 10)

        self.assertEqual(release.get("tag_name"), "v0.19.1")

    def test_custom_version_requires_api(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".download_bytes", side_effect=OSError("offline")
        ), self.assertRaises(OSError):
            fetch_restic_release_metadata("v0.18.0", 10)


class ResticDownloadVerifyTests(unittest.TestCase):
    def test_trusted_manifest_sha256_passes_without_extra_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = write_fake_archive(temp_dir)
            with mock.patch(
                DOWNLOAD_MODULE + ".download_bytes",
                side_effect=AssertionError("no checksum fetch"),
            ):
                verify_restic_download(TRUSTED_TARGET, archive_path)

    def test_checksum_mismatch_rejects_install(self):
        server = FakePluginServer()
        bad_target = ResticDownloadTarget(
            asset_url=TRUSTED_TARGET.asset_url,
            asset_name=TRUSTED_TARGET.asset_name,
            asset_keyword=TRUSTED_TARGET.asset_keyword,
            output_name=TRUSTED_TARGET.output_name,
            target_path="restic.exe",
            timeout=10,
            version="v0.19.1",
            sha256="0" * 64,
        )
        installed = mock.Mock()

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            DOWNLOAD_MODULE + ".download_bytes", return_value=FAKE_BINARY
        ), mock.patch(DOWNLOAD_MODULE + ".install_restic_archive", installed):
            write_fake_archive(temp_dir)
            with self.assertRaises(BackupProblem) as error:
                install_restic_from_urls(server, {}, bad_target)

        self.assertEqual(
            error.exception.i18n_key, "error.restic.auto_download_failed"
        )
        installed.assert_not_called()

    def test_checksum_unavailable_rejects_install(self):
        unreachable = ResticDownloadTarget(
            asset_url=(
                "https://github.com/restic/restic/releases/download/v0.18.0/"
                "restic_0.18.0_linux_amd64.bz2"
            ),
            asset_name="restic_0.18.0_linux_amd64.bz2",
            asset_keyword="linux_amd64.bz2",
            output_name="restic",
            target_path="restic",
            timeout=10,
            version="v0.18.0",
            sha256="",
        )

        with mock.patch(
            DOWNLOAD_MODULE + ".download_bytes", side_effect=OSError("offline")
        ), self.assertRaises(BackupProblem) as error:
            verify_restic_download(unreachable, "archive.bz2")

        self.assertEqual(
            error.exception.i18n_key, "error.restic.checksum_file_unavailable"
        )

    def test_expected_sha256_falls_back_to_official_sums(self):
        sums = "{}  restic_0.18.0_linux_amd64.bz2\n".format(FAKE_BINARY_SHA256)
        target = ResticDownloadTarget(
            asset_url=(
                "https://github.com/restic/restic/releases/download/v0.18.0/"
                "restic_0.18.0_linux_amd64.bz2"
            ),
            asset_name="restic_0.18.0_linux_amd64.bz2",
            asset_keyword="linux_amd64.bz2",
            output_name="restic",
            target_path="restic",
            timeout=10,
            version="v0.18.0",
            sha256="",
        )

        with mock.patch(
            DOWNLOAD_MODULE + ".download_bytes", return_value=sums.encode("utf-8")
        ):
            self.assertEqual(expected_restic_sha256(target), FAKE_BINARY_SHA256)

    def test_extract_sha256_from_sums_matches_name_variants(self):
        sums = (
            "{}  restic_0.18.0_linux_amd64.bz2\n".format(FAKE_BINARY_SHA256)
            + "{} *restic_0.18.0_windows_amd64.zip\n".format("f" * 64)
            + "{}  other-file\n".format("0" * 64)
        )

        self.assertEqual(
            extract_sha256_from_sums(sums, "restic_0.18.0_linux_amd64.bz2"),
            FAKE_BINARY_SHA256,
        )
        self.assertEqual(
            extract_sha256_from_sums(sums, "restic_0.18.0_windows_amd64.zip"), "f" * 64
        )
        self.assertEqual(extract_sha256_from_sums(sums, "missing-file"), "")
        self.assertEqual(extract_sha256_from_sums("nothex  file\n", "file"), "")


class DefaultVersionTests(unittest.TestCase):
    def test_default_download_version_is_trusted_version(self):
        self.assertEqual(get_default_restic_version(), "v0.19.1")
        self.assertEqual(
            build_default_config()["restic"]["download_version"],
            get_default_restic_version(),
        )


class ResticInstallArchiveTests(unittest.TestCase):
    def build_zip_archive(self, temp_dir: str) -> str:
        archive_path = os.path.join(temp_dir, "restic-download.zip")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("restic.exe", FAKE_BINARY)
        return archive_path

    def build_bz2_archive(self, temp_dir: str) -> str:
        archive_path = os.path.join(temp_dir, "restic-download.bz2")
        with open(archive_path, "wb") as file:
            file.write(bz2.compress(FAKE_BINARY))
        return archive_path

    def test_install_zip_writes_target_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self.build_zip_archive(temp_dir)
            target_path = os.path.join(temp_dir, "restic.exe")

            install_restic_archive(archive_path, ".zip", "restic.exe", target_path)

            with open(target_path, "rb") as file:
                self.assertEqual(file.read(), FAKE_BINARY)
            self.assertEqual(
                [name for name in os.listdir(temp_dir) if ".tmp-" in name], []
            )

    def test_install_bz2_writes_target_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self.build_bz2_archive(temp_dir)
            target_path = os.path.join(temp_dir, "restic")

            install_restic_archive(archive_path, ".bz2", "restic", target_path)

            with open(target_path, "rb") as file:
                self.assertEqual(file.read(), FAKE_BINARY)

    def test_install_interruption_keeps_target_absent_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self.build_zip_archive(temp_dir)
            target_path = os.path.join(temp_dir, "restic.exe")

            with mock.patch(
                DOWNLOAD_MODULE + ".os.replace", side_effect=OSError("interrupted")
            ), self.assertRaises(OSError):
                install_restic_archive(archive_path, ".zip", "restic.exe", target_path)

            self.assertFalse(os.path.exists(target_path))
            self.assertEqual(
                [name for name in os.listdir(temp_dir) if ".tmp-" in name], []
            )

    def test_install_failure_keeps_existing_binary_intact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = os.path.join(temp_dir, "restic")
            with open(target_path, "wb") as file:
                file.write(b"old-binary")
            archive_path = self.build_bz2_archive(temp_dir)

            with mock.patch(
                DOWNLOAD_MODULE + ".os.replace", side_effect=OSError("interrupted")
            ), self.assertRaises(OSError):
                install_restic_archive(archive_path, ".bz2", "restic", target_path)

            with open(target_path, "rb") as file:
                self.assertEqual(file.read(), b"old-binary")


def write_fake_archive(temp_dir: str) -> str:
    archive_path = os.path.join(temp_dir, "restic-download.zip")
    with open(archive_path, "wb") as file:
        file.write(FAKE_BINARY)
    return archive_path


if __name__ == "__main__":
    unittest.main()
