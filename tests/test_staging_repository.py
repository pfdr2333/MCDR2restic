# -*- coding: utf-8 -*-
import time

try:
    from .support import (
        BackupProblem,
        ResticCommandResult,
        create_runtime,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        ResticCommandResult,
        create_runtime,
        mock,
        unittest,
    )

from mcdr2restic.core.models import BackupCanceled

from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.staging import staging_repository
from mcdr2restic.staging.staging_config import (
    get_staging_settings,
    resolve_staging_paths,
)
from mcdr2restic.staging.staging_repository import (
    RepoProbeStatus,
    classify_repo_failure,
    compare_restic_times,
    cursor_from_snapshots,
    latest_snapshot_time,
    make_remote_probe_deadline,
    normalize_snapshot,
    parse_restic_time,
    parse_snapshots,
    probe_repository,
    select_pending_snapshots,
)


def result(return_code, stdout="", stderr=""):
    return ResticCommandResult("staging", [], return_code, stdout, stderr, 0.0)


class RepoProbeTest(unittest.TestCase):
    def test_ok_probe_parses_config(self):
        runtime = create_runtime()
        with mock.patch.object(
            staging_repository,
            "run_restic_command",
            return_value=result(0, '{"chunker_polynomial":"abc"}'),
        ):
            probe = probe_repository(runtime, {})

        self.assertEqual(probe.status, RepoProbeStatus.OK)
        self.assertEqual(probe.config["chunker_polynomial"], "abc")

    def test_failure_classification(self):
        self.assertEqual(
            classify_repo_failure(result(1, stderr="Fatal: wrong password or no key found")),
            RepoProbeStatus.AUTH,
        )
        self.assertEqual(
            classify_repo_failure(
                result(1, stderr="repository does not exist: unable to open config file")
            ),
            RepoProbeStatus.MISSING,
        )
        self.assertEqual(
            classify_repo_failure(result(1, stderr="dial tcp: connection refused")),
            RepoProbeStatus.UNREACHABLE,
        )

    def test_s3_auth_errors_are_classified_as_auth(self):
        # S3 鉴权/签名错误必须停止自动重试，而不是按不可达无限退避（E2E P2-3）
        samples = (
            "Fatal: unable to open config file: AccessDenied: Access Denied",
            "Fatal: unable to open config file: SignatureDoesNotMatch: "
            "The request signature we calculated does not match the signature you provided",
            "Fatal: unable to open config file: InvalidAccessKeyId: "
            "The AWS Access Key Id you provided does not exist in our records",
            "Fatal: unable to open config file: HEAD returned 403 Forbidden",
        )
        for stderr in samples:
            with self.subTest(stderr=stderr):
                self.assertEqual(
                    classify_repo_failure(result(1, stderr=stderr)),
                    RepoProbeStatus.AUTH,
                )

    def test_s3_quota_errors_are_classified_as_quota(self):
        samples = (
            "Fatal: unable to open config file: QuotaExceeded: quota exceeded for bucket",
            "Fatal: unable to open config file: EntityTooLarge: Your proposed upload "
            "exceeds the maximum allowed size",
            "Fatal: unable to open config file: InsufficientStorage: storage is full",
        )
        for stderr in samples:
            with self.subTest(stderr=stderr):
                self.assertEqual(
                    classify_repo_failure(result(1, stderr=stderr)),
                    RepoProbeStatus.QUOTA,
                )

    def test_probe_timeout_is_classified_as_unreachable(self):
        # 探测超时归为不可达而不是把异常抛给调用方（E2E P2-2）
        runtime = create_runtime()
        with mock.patch.object(
            staging_repository,
            "run_restic_command",
            side_effect=BackupProblem(
                i18n_key="error.restic.timeout",
                phase="staging-cat-config",
                seconds=30,
                output="",
            ),
        ):
            probe = probe_repository(
                runtime, {}, deadline=time.monotonic() + 30
            )

        self.assertEqual(probe.status, RepoProbeStatus.UNREACHABLE)
        self.assertIn("restic", probe.error)

    def test_probe_cancel_still_propagates(self):
        runtime = create_runtime()
        with (
            mock.patch.object(
                staging_repository,
                "run_restic_command",
                side_effect=BackupCanceled(i18n_key="error.backup.cancel_requested"),
            ),
            self.assertRaises(BackupCanceled),
        ):
            probe_repository(runtime, {}, deadline=time.monotonic() + 30)

    def test_remote_probe_deadline_zero_disables_timeout(self):
        self.assertIsNone(make_remote_probe_deadline(0))
        self.assertIsNotNone(make_remote_probe_deadline(30))

    def test_unreadable_config_is_not_treated_as_ok(self):
        runtime = create_runtime()
        with mock.patch.object(
            staging_repository, "run_restic_command", return_value=result(0, "garbage")
        ):
            probe = probe_repository(runtime, {})

        self.assertEqual(probe.status, RepoProbeStatus.UNREACHABLE)


class EnsureLocalRepositoryTest(unittest.TestCase):
    def test_local_missing_remote_exists_initializes_with_chunker_params(self):
        runtime = create_runtime()
        server = mock.Mock()
        cfg = build_default_config()
        cfg["staging"].update(
            {
                "enabled": True,
                "repository": "/data/staging",
                "password": "staging-pass",
                "password_file": "",
            }
        )
        cfg["restic"].update(
            {"repository": "/remote/repo", "password": "remote-pass"}
        )
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        probes = [
            staging_repository.RepoProbe(staging_repository.RepoProbeStatus.MISSING),
            staging_repository.RepoProbe(
                staging_repository.RepoProbeStatus.OK,
                {"chunker_polynomial": "poly"},
            ),
            staging_repository.RepoProbe(
                staging_repository.RepoProbeStatus.OK,
                {"chunker_polynomial": "poly"},
            ),
        ]

        with (
            mock.patch.object(
                staging_repository, "probe_repository", side_effect=probes
            ),
            mock.patch.object(
                staging_repository, "run_initialization"
            ) as initialize,
        ):
            local_cfg = staging_repository.ensure_local_repository(
                runtime, server, cfg["restic"], settings, paths
            )

        initialize.assert_called_once()
        init_cfg = initialize.call_args.args[2]
        init_args = initialize.call_args.args[3]
        self.assertEqual(
            init_args,
            ["init", "--from-repo", "/remote/repo", "--copy-chunker-params"],
        )
        self.assertEqual(init_cfg["password"], "staging-pass")
        self.assertEqual(
            init_cfg["environment"]["RESTIC_FROM_PASSWORD"], "remote-pass"
        )
        self.assertEqual(local_cfg["password"], "staging-pass")


class EnsureRemoteRepositoryForCopyTest(unittest.TestCase):
    def make_context(self):
        runtime = create_runtime()
        server = mock.Mock()
        cfg = build_default_config()
        cfg["staging"].update(
            {
                "enabled": True,
                "repository": "/data/staging",
                "password": "staging-pass",
                "password_file": "",
                "remote_probe_timeout_seconds": 30,
            }
        )
        cfg["restic"].update(
            {"repository": "/remote/repo", "password": "remote-pass"}
        )
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        return runtime, server, cfg, settings, paths

    def run_ensure(self, probe_status, probe_error="boom"):
        runtime, server, cfg, settings, paths = self.make_context()
        with mock.patch.object(
            staging_repository,
            "probe_repository",
            return_value=staging_repository.RepoProbe(probe_status, error=probe_error),
        ) as probe:
            if probe_status == RepoProbeStatus.OK:
                result = staging_repository.ensure_remote_repository_for_copy(
                    runtime, server, cfg["restic"], settings, paths
                )
                return result, probe
            with self.assertRaises(BackupProblem) as ctx:
                staging_repository.ensure_remote_repository_for_copy(
                    runtime, server, cfg["restic"], settings, paths
                )
            return ctx.exception, probe

    def test_ok_remote_returns_config_without_init(self):
        runtime, server, cfg, settings, paths = self.make_context()
        with mock.patch.object(
            staging_repository,
            "probe_repository",
            return_value=staging_repository.RepoProbe(RepoProbeStatus.OK),
        ):
            remote_cfg, created = (
                staging_repository.ensure_remote_repository_for_copy(
                    runtime, server, cfg["restic"], settings, paths
                )
            )

        self.assertEqual(
            remote_cfg["repository"], cfg["restic"]["repository"]
        )
        self.assertFalse(created)

    def test_auth_failure_raises_remote_auth(self):
        exc, _ = self.run_ensure(RepoProbeStatus.AUTH)
        self.assertEqual(exc.i18n_key, "error.staging.remote_auth")

    def test_quota_failure_raises_remote_quota(self):
        # 配额错误与鉴权错误一样必须停止自动重试（E2E P2-3）
        exc, _ = self.run_ensure(RepoProbeStatus.QUOTA)
        self.assertEqual(exc.i18n_key, "error.staging.remote_quota")

    def test_unreachable_failure_raises_remote_unreachable(self):
        exc, _ = self.run_ensure(RepoProbeStatus.UNREACHABLE)
        self.assertEqual(exc.i18n_key, "error.staging.remote_unreachable")

    def test_probe_receives_short_deadline(self):
        runtime, server, cfg, settings, paths = self.make_context()
        with mock.patch.object(
            staging_repository,
            "probe_repository",
            return_value=staging_repository.RepoProbe(RepoProbeStatus.OK),
        ) as probe:
            staging_repository.ensure_remote_repository_for_copy(
                runtime, server, cfg["restic"], settings, paths
            )

        deadline = probe.call_args.kwargs["deadline"]
        self.assertIsNotNone(deadline)
        self.assertLessEqual(deadline - time.monotonic(), 30.0)


class SnapshotParsingTest(unittest.TestCase):
    def test_parse_array_and_line_delimited_json(self):
        array_text = '[{"id":"aaa","short_id":"aaa","time":"2026-01-01T00:00:00Z"}]'
        line_text = (
            '{"id":"aaa","time":"2026-01-01T00:00:00Z"}\n'
            '{"id":"bbb","time":"2026-01-02T00:00:00Z"}\n'
        )

        self.assertEqual([item["id"] for item in parse_snapshots(array_text)], ["aaa"])
        self.assertEqual(
            [item["id"] for item in parse_snapshots(line_text)], ["aaa", "bbb"]
        )

    def test_normalize_rejects_items_without_id_or_time(self):
        self.assertIsNone(normalize_snapshot({"id": "aaa"}))
        self.assertIsNone(normalize_snapshot({"time": "2026-01-01T00:00:00Z"}))
        self.assertEqual(
            normalize_snapshot({"id": "abcdefgh1234", "time": "2026-01-01T00:00:00Z"})[
                "short_id"
            ],
            "abcdefgh",
        )

    def test_restic_time_parsing_handles_nanoseconds_and_numeric_offsets(self):
        self.assertEqual(
            parse_restic_time("2026-01-01T08:00:00.123456789+0800").isoformat(),
            "2026-01-01T00:00:00.123456+00:00",
        )
        self.assertEqual(
            parse_restic_time("2026-01-01T00:00:00Z").isoformat(),
            "2026-01-01T00:00:00+00:00",
        )
        self.assertIsNone(parse_restic_time("not-a-time"))

    def test_compare_falls_back_to_text_when_unparsable(self):
        self.assertGreater(compare_restic_times("b", "a"), 0)
        self.assertEqual(compare_restic_times("a", "a"), 0)


SNAPSHOTS = [
    {"id": "s1", "time": "2026-01-01T00:00:00Z"},
    {"id": "s2", "time": "2026-01-02T00:00:00Z"},
    {"id": "s3", "time": "2026-01-02T00:00:00Z"},
    {"id": "s4", "time": "2026-01-03T00:00:00Z"},
]


class CursorTest(unittest.TestCase):

    def test_no_cursor_selects_all_in_time_order(self):
        self.assertEqual(
            [item["id"] for item in select_pending_snapshots(SNAPSHOTS, None)],
            ["s1", "s2", "s3", "s4"],
        )

    def test_cursor_selects_only_later_snapshots(self):
        pending = select_pending_snapshots(
            SNAPSHOTS, "2026-01-02T00:00:00Z", ["s2"]
        )

        self.assertEqual([item["id"] for item in pending], ["s3", "s4"])

    def test_same_time_snapshots_use_id_set_to_avoid_resurrection(self):
        pending = select_pending_snapshots(
            SNAPSHOTS, "2026-01-02T00:00:00Z", ["s2", "s3"]
        )

        self.assertEqual([item["id"] for item in pending], ["s4"])

    def test_cursor_and_latest_time_use_max_snapshot_time(self):
        latest, ids = cursor_from_snapshots(SNAPSHOTS)

        self.assertEqual(latest, "2026-01-03T00:00:00Z")
        self.assertEqual(ids, ["s4"])
        self.assertEqual(latest_snapshot_time(SNAPSHOTS), latest)
        self.assertIsNone(latest_snapshot_time([]))


if __name__ == "__main__":
    unittest.main()
