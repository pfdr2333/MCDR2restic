# -*- coding: utf-8 -*-
try:
    from .support import (
        ResticCommandResult,
        create_runtime,
        mock,
        unittest,
    )
except ImportError:
    from support import ResticCommandResult, create_runtime, mock, unittest

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
