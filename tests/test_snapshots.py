import sqlite3

try:
    from .support import (
        BackupProblem,
        FakeCommandSource,
        FakePluginServer,
        FakeServer,
        ProcessTimeoutState,
        RestoreTaskRecord,
        TerminateResult,
        add_restore_task,
        assert_snapshot_import_finished,
        build_snapshot_cache_key,
        clear_restore_tasks,
        closing,
        import_restic_snapshots_to_sql,
        commit_refreshed_snapshot_cache,
        execute_column_addition,
        insert_snapshot_row,
        io,
        iter_json_array_stream,
        list_restore_tasks,
        make_source_translate,
        mock,
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
        RestoreTaskRecord,
        TerminateResult,
        add_restore_task,
        assert_snapshot_import_finished,
        build_snapshot_cache_key,
        clear_restore_tasks,
        closing,
        import_restic_snapshots_to_sql,
        execute_column_addition,
        commit_refreshed_snapshot_cache,
        insert_snapshot_row,
        io,
        iter_json_array_stream,
        list_restore_tasks,
        make_source_translate,
        mock,
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

    def test_import_failure_terminates_restic_process(self):
        process = mock.Mock()
        process.poll.return_value = None
        timer = mock.Mock()
        stderr_thread = mock.Mock()

        with mock.patch(
            "mcdr2restic.snapshots.snapshot_importer.start_restic_snapshot_process",
            return_value=process,
        ), mock.patch(
            "mcdr2restic.snapshots.snapshot_importer.start_snapshot_stderr_reader",
            return_value=stderr_thread,
        ), mock.patch(
            "mcdr2restic.snapshots.snapshot_importer.start_process_timeout_timer",
            return_value=(ProcessTimeoutState(threading.Event()), timer),
        ), mock.patch(
            "mcdr2restic.snapshots.snapshot_importer.import_snapshot_stdout",
            side_effect=RuntimeError("parse boom"),
        ), mock.patch(
            "mcdr2restic.snapshots.snapshot_importer.terminate_process"
        ) as terminate, self.assertRaises(RuntimeError):
            import_restic_snapshots_to_sql({}, mock.Mock(), "cache", 60)

        terminate.assert_called_once_with(process)
        timer.cancel.assert_called_once_with()
        stderr_thread.join.assert_called_once()


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
                server,
                snapshot_cfg,
                "cache-a",
                RestoreTaskRecord("abcdef12", "file", "/world/level.dat"),
            )
            output = restore_tasks_output(
                server, snapshot_cfg, "cache-a", "en_us", "!!restic"
            )

            self.assertIn("MCDR2Restic Restore Tasks", output)
            self.assertIn(
                "{}. [file] abcdef12 -> /world/level.dat".format(task_id), output
            )

    def test_restore_task_target_directory_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {"database": "snapshots.sqlite3"}

            task_id = add_restore_task(
                server,
                snapshot_cfg,
                "cache-a",
                RestoreTaskRecord("abcdef12", "full", "/", "/tmp/out"),
            )
            rows = list_restore_tasks(server, snapshot_cfg, "cache-a")
            output = restore_tasks_output(
                server, snapshot_cfg, "cache-a", "zh_cn", "!!restic"
            )

            self.assertEqual(rows[0]["target_directory"], "/tmp/out")
            self.assertIn(
                "{}. [整份快照] abcdef12 -> / (恢复到 /tmp/out)".format(task_id),
                output,
            )

    def test_restore_tasks_output_accepts_source_translate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {"database": "snapshots.sqlite3"}
            source = FakeCommandSource(language="zh_cn")
            plugin_server = FakePluginServer(language="en_us")

            task_id = add_restore_task(
                server,
                snapshot_cfg,
                "cache-a",
                RestoreTaskRecord("abcdef12", "file", "/world/level.dat"),
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
            add_restore_task(
                server, snapshot_cfg, "cache-a", RestoreTaskRecord("a", "full", "/")
            )
            add_restore_task(
                server, snapshot_cfg, "cache-b", RestoreTaskRecord("b", "full", "/")
            )

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

    def test_column_addition_tolerates_duplicate_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            with closing(open_snapshot_db(server, {"database": "db.sqlite3"})) as conn:
                # 列已存在时不得抛错（并发建表场景），其他 OperationalError 仍需抛出
                execute_column_addition(
                    conn,
                    "ALTER TABLE restore_tasks ADD COLUMN cache_key TEXT NOT NULL DEFAULT ''",
                )
                with self.assertRaises(sqlite3.OperationalError):
                    execute_column_addition(conn, "ALTER TABLE no_such_table ADD COLUMN x TEXT")

    def test_refresh_commit_prunes_orphan_cache_keys(self):
        temp_key = "new-key:refresh:111:222"
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            with closing(open_snapshot_db(server, {"database": "db.sqlite3"})) as conn:
                conn.execute(
                    "INSERT INTO snapshot_meta (cache_key) VALUES ('old-key'), ('pending-key')"
                )
                conn.execute(
                    "INSERT INTO snapshots (cache_key, id, short_id, time_text)"
                    " VALUES ('old-key', 's1', 's1', '2024-01-01T00:00:00Z'),"
                    " ('pending-key', 's2', 's2', '2024-01-02T00:00:00Z'),"
                    " ('new-key:refresh:333:444', 'stale', 'stale', '2024-01-03T00:00:00Z'),"
                    " (?, 's3', 's3', '2024-01-04T00:00:00Z')",
                    (temp_key,),
                )
                conn.execute(
                    "INSERT INTO restore_tasks (created_at_text, snapshot, item_type, include_path, cache_key)"
                    " VALUES ('now', 's2', 'full', '/', 'pending-key')"
                )

                commit_refreshed_snapshot_cache(conn, "new-key", temp_key, 1, 0.5)

                remaining_meta = {
                    str(row[0])
                    for row in conn.execute("SELECT cache_key FROM snapshot_meta")
                }
                remaining_rows = {
                    str(row[0])
                    for row in conn.execute("SELECT DISTINCT cache_key FROM snapshots")
                }

        self.assertEqual(remaining_meta, {"new-key", "pending-key"})
        # 崩溃残留的 :refresh: 临时行被保守保留，避免误伤并发刷新
        self.assertEqual(
            remaining_rows,
            {"new-key", "pending-key", "new-key:refresh:333:444"},
        )
