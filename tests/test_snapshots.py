try:
    from .support import (
        BackupProblem,
        FakeCommandSource,
        FakePluginServer,
        FakeServer,
        ProcessTimeoutState,
        TerminateResult,
        add_restore_task,
        assert_snapshot_import_finished,
        build_snapshot_cache_key,
        clear_restore_tasks,
        closing,
        insert_snapshot_row,
        io,
        iter_json_array_stream,
        list_restore_tasks,
        make_source_translate,
        open_snapshot_db,
        read_snapshot_page,
        restore_tasks_output,
        tempfile,
        threading,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakeCommandSource,
        FakePluginServer,
        FakeServer,
        ProcessTimeoutState,
        TerminateResult,
        add_restore_task,
        assert_snapshot_import_finished,
        build_snapshot_cache_key,
        clear_restore_tasks,
        closing,
        insert_snapshot_row,
        io,
        iter_json_array_stream,
        list_restore_tasks,
        make_source_translate,
        open_snapshot_db,
        read_snapshot_page,
        restore_tasks_output,
        tempfile,
        threading,
        unittest,
    )


class SnapshotCacheTests(unittest.TestCase):
    def test_cache_key_hashes_secrets_and_is_stable(self):
        cfg = {
            "repository": "/repo",
            "password": "secret-password",
            "environment": {"TOKEN": "abc", "EMPTY": None},
        }
        same_cfg_different_order = {
            "password": "secret-password",
            "environment": {"EMPTY": None, "TOKEN": "abc"},
            "repository": "/repo",
        }

        cache_key = build_snapshot_cache_key(cfg)

        self.assertEqual(cache_key, build_snapshot_cache_key(same_cfg_different_order))
        self.assertNotIn("secret-password", cache_key)
        self.assertNotIn("abc", cache_key)
        self.assertEqual(len(cache_key), 64)


class SnapshotImporterTests(unittest.TestCase):
    def test_iter_json_array_stream_reads_snapshot_objects(self):
        stream = io.StringIO('[{"id":"a"}, {"id":"b", "paths":["world"]}]')

        self.assertEqual(
            list(iter_json_array_stream(stream)),
            [{"id": "a"}, {"id": "b", "paths": ["world"]}],
        )

    def test_timeout_error_includes_termination_failure(self):
        timeout_state = ProcessTimeoutState(
            threading.Event(), TerminateResult(error="kill failed")
        )
        timeout_state.timed_out.set()

        with self.assertRaises(BackupProblem) as error:
            assert_snapshot_import_finished(3, timeout_state, 0, "")

        self.assertIn("终止失败", str(error.exception))
        self.assertIn("kill failed", str(error.exception))

    def test_snapshot_return_code_guides_repository_initialization(self):
        timeout_state = ProcessTimeoutState(threading.Event())

        with self.assertRaises(BackupProblem) as error:
            assert_snapshot_import_finished(
                3,
                timeout_state,
                1,
                "Fatal: unable to open config file: config file does not exist",
                "!!backup",
            )

        self.assertEqual(
            error.exception.i18n_key,
            "error.snapshot.return_code.repository_not_initialized",
        )
        self.assertEqual(error.exception.i18n_params["init_command"], "!!backup init")


class SnapshotDatabaseTests(unittest.TestCase):
    def test_read_snapshot_page_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {"database": "snapshots.sqlite3"}
            with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
                insert_snapshot_row(
                    conn, "cache", {"id": "old", "time": "2024-01-01T00:00:00Z"}
                )
                insert_snapshot_row(
                    conn, "cache", {"id": "new", "time": "2024-01-02T00:00:00Z"}
                )
                conn.commit()

            page = read_snapshot_page(
                server, "cache", page=1, page_size=1, snapshot_cfg=snapshot_cfg
            )

            self.assertEqual(page["total"], 2)
            self.assertEqual(page["rows"][0]["id"], "new")

    def test_restore_tasks_output_uses_repository_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {"database": "snapshots.sqlite3"}

            task_id = add_restore_task(
                server, snapshot_cfg, "cache-a", "abcdef12", "file", "/world/level.dat"
            )
            output = restore_tasks_output(
                server, snapshot_cfg, "cache-a", "en_us", "!!restic"
            )

            self.assertIn("MCDR2Restic Restore Tasks", output)
            self.assertIn(
                "{}. [file] abcdef12 -> /world/level.dat".format(task_id), output
            )

    def test_restore_tasks_output_accepts_source_translate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {"database": "snapshots.sqlite3"}
            source = FakeCommandSource(language="zh_cn")
            plugin_server = FakePluginServer(language="en_us")

            task_id = add_restore_task(
                server, snapshot_cfg, "cache-a", "abcdef12", "file", "/world/level.dat"
            )
            output = restore_tasks_output(
                server,
                snapshot_cfg,
                "cache-a",
                make_source_translate(source, plugin_server),
                "!!restic",
            )

            self.assertIn("MCDR2Restic 恢复任务列表", output)
            self.assertIn(
                "{}. [文件] abcdef12 -> /world/level.dat".format(task_id), output
            )

    def test_restore_tasks_are_isolated_by_cache_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {"database": "snapshots.sqlite3"}
            add_restore_task(server, snapshot_cfg, "cache-a", "a", "full", "/")
            add_restore_task(server, snapshot_cfg, "cache-b", "b", "full", "/")

            self.assertEqual(
                [
                    row["snapshot"]
                    for row in list_restore_tasks(server, snapshot_cfg, "cache-a")
                ],
                ["a"],
            )
            self.assertEqual(clear_restore_tasks(server, snapshot_cfg, "cache-a"), 1)
            self.assertEqual(
                [
                    row["snapshot"]
                    for row in list_restore_tasks(server, snapshot_cfg, "cache-b")
                ],
                ["b"],
            )
