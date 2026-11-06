import bz2
import hashlib
import os
import time
import zipfile

try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        RESTIC_DOWNLOAD_TIMEOUT_SECONDS,
        ResticDownloadTarget,
        ResticVersionProbeError,
        build_download_urls,
        build_self_mirror_url,
        compare_restic_versions,
        download_and_install_restic,
        download_bytes,
        download_file,
        ensure_default_restic_executable_available,
        install_restic_archive,
        install_restic_from_urls,
        make_download_progress_reporter,
        mock,
        parse_restic_version_number,
        probe_restic_version,
        select_restic_release_asset,
        tempfile,
        unittest,
        verify_restic_download,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakePluginServer,
        RESTIC_DOWNLOAD_TIMEOUT_SECONDS,
        ResticDownloadTarget,
        ResticVersionProbeError,
        build_download_urls,
        build_self_mirror_url,
        compare_restic_versions,
        download_and_install_restic,
        download_bytes,
        download_file,
        ensure_default_restic_executable_available,
        install_restic_archive,
        install_restic_from_urls,
        make_download_progress_reporter,
        mock,
        parse_restic_version_number,
        probe_restic_version,
        select_restic_release_asset,
        tempfile,
        unittest,
        verify_restic_download,
    )


DOWNLOAD_MODULE = "mcdr2restic.restic.restic_download"

FAKE_BINARY = b"restic-fake-binary"
FAKE_BINARY_SHA256 = hashlib.sha256(FAKE_BINARY).hexdigest()

GITHUB_ASSET_URL = (
    "https://github.com/restic/restic/releases/download/v0.19.1/"
    "restic_0.19.1_windows_amd64.zip"
)
SELF_MIRROR_ASSET_URL = (
    "https://dl.m2r.pfdr.cc/v0.19.1/restic_0.19.1_windows_amd64.zip"
)

TRUSTED_TARGET = ResticDownloadTarget(
    asset_url=GITHUB_ASSET_URL,
    asset_name="restic_0.19.1_windows_amd64.zip",
    asset_keyword="windows_amd64.zip",
    output_name="restic.exe",
    target_path="restic.exe",
    timeout=10,
    version="v0.19.1",
    sha256=FAKE_BINARY_SHA256,
)

PINNED_RELEASE = {
    "tag_name": "v0.19.1",
    "assets": [
        {
            "name": "restic_0.19.1_windows_amd64.zip",
            "browser_download_url": GITHUB_ASSET_URL,
            "sha256": FAKE_BINARY_SHA256,
        }
    ],
}


class ManifestDrivenDownloadTests(unittest.TestCase):
    def test_download_target_comes_from_pinned_manifest(self):
        target_holder = {}

        def capture_target(_server, target):
            target_holder["target"] = target

        with mock.patch(
            DOWNLOAD_MODULE + ".build_restic_fallback_release",
            return_value=PINNED_RELEASE,
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_restic_from_urls", side_effect=capture_target
        ):
            download_and_install_restic(
                FakePluginServer(), "windows_amd64.zip", "restic.exe", "restic.exe"
            )

        target = target_holder["target"]
        self.assertEqual(target.asset_url, GITHUB_ASSET_URL)
        self.assertEqual(target.version, "v0.19.1")
        self.assertEqual(target.sha256, FAKE_BINARY_SHA256)
        self.assertEqual(target.timeout, RESTIC_DOWNLOAD_TIMEOUT_SECONDS)

    def test_missing_manifest_fails_before_any_download(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".build_restic_fallback_release", return_value={}
        ), self.assertRaises(BackupProblem) as error:
            download_and_install_restic(
                FakePluginServer(), "windows_amd64.zip", "restic.exe", "restic.exe"
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restic.release_manifest_missing"
        )


class DownloadCandidateUrlTests(unittest.TestCase):
    def test_candidate_order_github_mirror_then_public_prefixes(self):
        proxies = ("https://gh-proxy.com/", "https://ghproxy.net/")

        urls = build_download_urls(GITHUB_ASSET_URL, proxies)

        self.assertEqual(
            urls,
            [
                GITHUB_ASSET_URL,
                SELF_MIRROR_ASSET_URL,
                "https://gh-proxy.com/" + GITHUB_ASSET_URL,
                "https://ghproxy.net/" + GITHUB_ASSET_URL,
            ],
        )

    def test_self_mirror_url_maps_release_assets(self):
        self.assertEqual(
            build_self_mirror_url(GITHUB_ASSET_URL, "https://dl.example.test/"),
            "https://dl.example.test/v0.19.1/restic_0.19.1_windows_amd64.zip",
        )
        self.assertEqual(build_self_mirror_url("https://example.com/a.zip"), "")


class FakeStreamingResponse:
    """按预置块返回数据的假响应，模拟慢速滴流连接。"""

    def __init__(self, chunks, delay=0.0):
        self.chunks = list(chunks)
        self.delay = delay
        self.headers = {"Content-Length": str(sum(len(chunk) for chunk in self.chunks))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        if not self.chunks:
            return b""
        time.sleep(self.delay)
        return self.chunks.pop(0)


class StreamingDownloadTests(unittest.TestCase):
    def test_attempt_deadline_wraps_timeout_with_url_context(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".RESTIC_DOWNLOAD_ATTEMPT_DEADLINE_SECONDS", -1
        ), tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            DOWNLOAD_MODULE + ".urllib.request.urlopen",
            return_value=FakeStreamingResponse([b"y" * 10]),
        ), self.assertRaises(
            BackupProblem
        ) as error:
            download_file(GITHUB_ASSET_URL, os.path.join(temp_dir, "archive.zip"), 10)

        self.assertEqual(error.exception.i18n_key, "error.restic.download_failed")
        self.assertIn("github.com", error.exception.i18n_params["url"])

    def test_slow_drip_download_stops_at_deadline_with_partial_file(self):
        def slow_urlopen(request, timeout):
            return FakeStreamingResponse([b"x" * 8, b"x" * 8], delay=0.3)

        with mock.patch(
            DOWNLOAD_MODULE + ".RESTIC_DOWNLOAD_ATTEMPT_DEADLINE_SECONDS", 0.1
        ), mock.patch(
            DOWNLOAD_MODULE + ".urllib.request.urlopen", side_effect=slow_urlopen
        ), tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            with self.assertRaises(BackupProblem):
                download_file(GITHUB_ASSET_URL, archive_path, 10)
            with open(archive_path, "rb") as file:
                self.assertEqual(file.read(), b"x" * 8)

    def test_mid_read_timeout_keeps_url_in_problem(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ), self.assertRaises(BackupProblem) as error:
            download_bytes(GITHUB_ASSET_URL, 10)

        self.assertEqual(error.exception.i18n_key, "error.restic.download_failed")
        self.assertIn("github.com", error.exception.i18n_params["url"])

    def test_progress_reporter_logs_interval_and_completion(self):
        server = FakePluginServer()
        report = make_download_progress_reporter(server)

        with mock.patch(DOWNLOAD_MODULE + ".RESTIC_PROGRESS_INTERVAL_SECONDS", 0):
            report(512, 1024)
            report(1024, 1024)

        self.assertEqual(len(server.logger.info_messages), 2)
        self.assertIn("50", server.logger.info_messages[0])
        self.assertIn("100", server.logger.info_messages[1])

    def test_progress_reporter_holds_back_rapid_updates(self):
        server = FakePluginServer()
        clock = iter([100.0, 100.5, 100.9, 106.0])

        with mock.patch(DOWNLOAD_MODULE + ".time") as fake_time:
            fake_time.monotonic.side_effect = lambda: next(clock)
            report = make_download_progress_reporter(server)
            report(100, 1000)
            report(150, 1000)
            report(200, 1000)

        self.assertEqual(len(server.logger.info_messages), 2)


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

        def fake_download(url, path, timeout, progress=None):
            with open(path, "wb") as file:
                file.write(FAKE_BINARY)

        with mock.patch(
            DOWNLOAD_MODULE + ".download_file", side_effect=fake_download
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_restic_archive", installed
        ), self.assertRaises(BackupProblem) as error:
            install_restic_from_urls(server, bad_target)

        self.assertEqual(
            error.exception.i18n_key, "error.restic.auto_download_failed"
        )
        installed.assert_not_called()

    def test_checksum_missing_in_manifest_rejects_install(self):
        unchecksummed = ResticDownloadTarget(
            asset_url=TRUSTED_TARGET.asset_url,
            asset_name=TRUSTED_TARGET.asset_name,
            asset_keyword=TRUSTED_TARGET.asset_keyword,
            output_name=TRUSTED_TARGET.output_name,
            target_path="restic.exe",
            timeout=10,
            version="v0.19.1",
            sha256="",
        )

        with self.assertRaises(BackupProblem) as error:
            verify_restic_download(unchecksummed, "archive.zip")

        self.assertEqual(
            error.exception.i18n_key, "error.restic.checksum_manifest_missing"
        )


class InstallCandidateFallbackTests(unittest.TestCase):
    def test_candidates_tried_in_order_until_success(self):
        server = FakePluginServer()
        tried = []

        def fake_download(url, path, timeout, progress=None):
            tried.append(url)
            if len(tried) < 3:
                raise BackupProblem(
                    i18n_key="error.restic.download_failed", url=url, error="boom"
                )
            with open(path, "wb") as file:
                file.write(FAKE_BINARY)

        with mock.patch(
            DOWNLOAD_MODULE + ".download_file", side_effect=fake_download
        ), mock.patch(DOWNLOAD_MODULE + ".install_restic_archive"):
            install_restic_from_urls(server, TRUSTED_TARGET)

        self.assertEqual(
            tried,
            [
                GITHUB_ASSET_URL,
                SELF_MIRROR_ASSET_URL,
                "https://gh-proxy.com/" + GITHUB_ASSET_URL,
            ],
        )

    def test_all_candidates_failed_raises_with_last_error(self):
        server = FakePluginServer()

        def failing_download(url, path, timeout, progress=None):
            raise BackupProblem(
                i18n_key="error.restic.download_failed", url=url, error="down"
            )

        with mock.patch(
            DOWNLOAD_MODULE + ".download_file", side_effect=failing_download
        ), self.assertRaises(BackupProblem) as error:
            install_restic_from_urls(server, TRUSTED_TARGET)

        self.assertEqual(
            error.exception.i18n_key, "error.restic.auto_download_failed"
        )


class EnsureDefaultResticAvailableTests(unittest.TestCase):
    def test_skips_install_when_system_restic_preferred_and_found(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": True}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=False
        ), mock.patch(
            DOWNLOAD_MODULE + ".find_system_restic", return_value="/usr/bin/restic"
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        install.assert_not_called()

    def test_installs_when_system_restic_preferred_but_missing(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": True}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=False
        ), mock.patch(
            DOWNLOAD_MODULE + ".find_system_restic", return_value=None
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        install.assert_called_once()


class ResticVersionHelpersTests(unittest.TestCase):
    def test_parse_version_number_accepts_v_prefix_and_padding(self):
        self.assertEqual(parse_restic_version_number("v0.19.1"), (0, 19, 1))
        self.assertEqual(parse_restic_version_number("0.18.0"), (0, 18, 0))
        self.assertEqual(parse_restic_version_number(" v0.19 "), (0, 19))

    def test_parse_version_number_rejects_unrecognizable_text(self):
        self.assertIsNone(parse_restic_version_number("latest"))
        self.assertIsNone(parse_restic_version_number(""))
        self.assertIsNone(parse_restic_version_number("v0.19.1-beta"))

    def test_compare_versions_pads_missing_segments(self):
        self.assertEqual(compare_restic_versions("0.19", "v0.19.0"), 0)
        self.assertEqual(compare_restic_versions("0.18.0", "v0.19.1"), -1)
        self.assertEqual(compare_restic_versions("0.20.0", "v0.19.1"), 1)

    def test_compare_versions_returns_zero_when_unparsable(self):
        self.assertEqual(compare_restic_versions("latest", "v0.19.1"), 0)
        self.assertEqual(compare_restic_versions("0.18.0", ""), 0)

    def test_probe_parses_version_from_command_output(self):
        completed = mock.Mock(
            stdout="restic 0.18.0 compiled with go1.23.4 on windows/amd64\n",
            stderr="",
        )

        with mock.patch(
            DOWNLOAD_MODULE + ".subprocess.run", return_value=completed
        ) as run:
            version = probe_restic_version("restic.exe")

        self.assertEqual(version, "0.18.0")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["restic.exe", "version"])

    def test_probe_accepts_v_prefixed_output(self):
        completed = mock.Mock(
            stdout="restic v0.19.1 compiled with go1.23.4\n", stderr=""
        )

        with mock.patch(DOWNLOAD_MODULE + ".subprocess.run", return_value=completed):
            self.assertEqual(probe_restic_version("restic"), "0.19.1")

    def test_probe_raises_when_output_unrecognizable(self):
        completed = mock.Mock(stdout="not-a-restic-binary", stderr="")

        with mock.patch(
            DOWNLOAD_MODULE + ".subprocess.run", return_value=completed
        ), self.assertRaises(ResticVersionProbeError):
            probe_restic_version("restic.exe")

    def test_probe_raises_when_process_unstartable(self):
        with mock.patch(
            DOWNLOAD_MODULE + ".subprocess.run", side_effect=OSError("boom")
        ), self.assertRaises(ResticVersionProbeError):
            probe_restic_version("missing-restic")


class EnsureDefaultResticUpgradeTests(unittest.TestCase):
    def test_upgrades_outdated_default_path_binary(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": False}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".probe_restic_version", return_value="0.18.0"
        ), mock.patch(
            DOWNLOAD_MODULE + ".get_default_restic_version", return_value="v0.19.1"
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        install.assert_called_once()
        self.assertTrue(
            any("开始自动升级" in m for m in server.logger.info_messages)
        )

    def test_keeps_newer_binary_without_downgrade(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": False}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".probe_restic_version", return_value="0.20.0"
        ), mock.patch(
            DOWNLOAD_MODULE + ".get_default_restic_version", return_value="v0.19.1"
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        install.assert_not_called()
        self.assertTrue(
            any("跳过自动升级" in m for m in server.logger.info_messages)
        )

    def test_keeps_binary_when_versions_equal(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": False}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".probe_restic_version", return_value="0.19.1"
        ), mock.patch(
            DOWNLOAD_MODULE + ".get_default_restic_version", return_value="v0.19.1"
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        install.assert_not_called()
        self.assertEqual(server.logger.info_messages, [])
        self.assertEqual(server.logger.warning_messages, [])

    def test_skips_probe_when_trusted_version_unparsable(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": False}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".get_default_restic_version", return_value="latest"
        ), mock.patch(DOWNLOAD_MODULE + ".probe_restic_version") as probe:
            ensure_default_restic_executable_available(server, cfg)

        probe.assert_not_called()

    def test_probe_failure_warns_without_raising(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": False}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".get_default_restic_version", return_value="v0.19.1"
        ), mock.patch(
            DOWNLOAD_MODULE + ".probe_restic_version",
            side_effect=ResticVersionProbeError("boom"),
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        install.assert_not_called()
        self.assertTrue(
            any("已跳过自动升级" in m for m in server.logger.warning_messages)
        )

    def test_upgrade_failure_warns_without_raising(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": False}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".probe_restic_version", return_value="0.18.0"
        ), mock.patch(
            DOWNLOAD_MODULE + ".get_default_restic_version", return_value="v0.19.1"
        ), mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform",
            side_effect=BackupProblem(
                i18n_key="error.restic.auto_download_failed", error="network down"
            ),
        ):
            ensure_default_restic_executable_available(server, cfg)

        self.assertTrue(
            any(
                "自动升级失败" in m and "0.18.0" in m
                for m in server.logger.warning_messages
            )
        )

    def test_system_restic_short_circuits_before_probe(self):
        server = FakePluginServer()
        cfg = {"executable": "restic", "use_path_restic": True}

        with mock.patch(
            DOWNLOAD_MODULE + ".os.path.exists", return_value=True
        ), mock.patch(
            DOWNLOAD_MODULE + ".find_system_restic", return_value="/usr/bin/restic"
        ), mock.patch(DOWNLOAD_MODULE + ".probe_restic_version") as probe, mock.patch(
            DOWNLOAD_MODULE + ".install_default_restic_for_platform"
        ) as install:
            ensure_default_restic_executable_available(server, cfg)

        probe.assert_not_called()
        install.assert_not_called()


class SelectReleaseAssetTests(unittest.TestCase):
    @staticmethod
    def asset(name):
        return {"name": name}

    def test_signature_files_are_never_selected(self):
        release = {
            "assets": [
                self.asset("restic_v0.19.1_windows_amd64.zip.asc"),
                self.asset("restic_v0.19.1_windows_amd64.zip.sig"),
                self.asset("restic_v0.19.1_windows_amd64.zip"),
            ]
        }

        selected = select_restic_release_asset(release, "windows_amd64")

        self.assertEqual(selected["name"], "restic_v0.19.1_windows_amd64.zip")

    def test_missing_asset_raises_localized_problem(self):
        release = {"assets": [self.asset("restic_v0.19.1_linux_amd64.bz2")]}

        with self.assertRaises(BackupProblem):
            select_restic_release_asset(release, "windows_amd64")


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
