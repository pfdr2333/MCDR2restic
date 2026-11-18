try:
    from .support import (
        command_handlers,
        ConfigError,
        create_runtime,
        FakePluginServer,
        mock,
        PermissiveCommandSource,
        unittest,
    )
except ImportError:
    from support import (
        command_handlers,
        ConfigError,
        create_runtime,
        FakePluginServer,
        mock,
        PermissiveCommandSource,
        unittest,
    )


class CommandHandlerTests(unittest.TestCase):
    def test_register_commands_deduplicates_and_trims_aliases(self):
        handlers = command_handlers.CommandHandlers.__new__(
            command_handlers.CommandHandlers
        )
        handlers.context = mock.Mock()
        handlers.context.get_command_root.return_value = "restic"
        handlers.build_command_tree = mock.Mock()
        handlers.build_command_tree.side_effect = lambda root: root
        server = mock.Mock()

        with mock.patch.object(
            command_handlers,
            "get_config_snapshot",
            return_value={
                "command": {"aliases": [" restic ", "backup", "", " backup "]}
            },
        ):
            handlers.register_commands(server)

        self.assertEqual(
            handlers.build_command_tree.call_args_list,
            [mock.call("restic"), mock.call("backup")],
        )
        self.assertEqual(
            server.register_command.call_args_list,
            [mock.call("restic"), mock.call("backup")],
        )

    def test_register_commands_tolerates_scalar_or_empty_command_section(self):
        handlers = command_handlers.CommandHandlers.__new__(
            command_handlers.CommandHandlers
        )
        handlers.context = mock.Mock()
        handlers.context.get_command_root.return_value = "restic"
        handlers.build_command_tree = mock.Mock(side_effect=lambda root: root)
        server = mock.Mock()

        for section in ("xx", None, 1):
            with mock.patch.object(
                command_handlers,
                "get_config_snapshot",
                return_value={"command": section},
            ):
                handlers.register_commands(server)

        self.assertEqual(
            handlers.build_command_tree.call_args_list,
            [mock.call("restic")] * 3,
        )

    def test_register_commands_ignores_non_list_aliases_with_warning(self):
        # P1-1：字符串 aliases 会被逐字符注册为单字符命令根，必须告警并忽略
        handlers = command_handlers.CommandHandlers.__new__(
            command_handlers.CommandHandlers
        )
        handlers.context = mock.Mock()
        handlers.context.get_command_root.return_value = "restic"
        handlers.build_command_tree = mock.Mock(side_effect=lambda root: root)
        server = mock.Mock()

        for bad in ("rs2", 7, {"a": 1}):
            with mock.patch.object(
                command_handlers,
                "get_config_snapshot",
                return_value={"command": {"aliases": bad}},
            ):
                handlers.register_commands(server)

        self.assertEqual(
            handlers.build_command_tree.call_args_list,
            [mock.call("restic")] * 3,
        )
        self.assertEqual(len(server.logger.warning.call_args_list), 3)


class CancelCommandTests(unittest.TestCase):
    @staticmethod
    def make_commands():
        from mcdr2restic.commands.backup_commands import BackupCommands
        from mcdr2restic.commands.command_context import CommandContext

        runtime = create_runtime()
        context = CommandContext(
            app_runtime=runtime,
            backup_runner_factory=lambda: None,
            maintenance_runner_factory=lambda: None,
            reload_services=mock.Mock(),
            wake_scheduler=mock.Mock(),
            snapshot_invalidator=mock.Mock(),
        )
        return BackupCommands(context), runtime

    def test_cancel_after_restic_exit_reports_finishing(self):
        # 备份刚结束（锁仍持有、restic 已退出）时不能声称"正在终止 restic 进程"（P3-1）
        commands, runtime = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())
        runtime.backup.lock.acquire()
        try:
            with mock.patch(
                "mcdr2restic.commands.backup_commands.request_cancel_current_backup"
            ):
                commands.command_cancel(source)
        finally:
            runtime.backup.lock.release()

        self.assertIn("restic 进程已结束", source.replies[-1])
        self.assertNotIn("正在终止 restic 进程", source.replies[-1])

    def test_cancel_terminates_running_backup(self):
        commands, runtime = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())
        runtime.backup.lock.acquire()
        try:
            with mock.patch(
                "mcdr2restic.commands.backup_commands.request_cancel_current_backup"
            ) as cancel_backup:
                commands.command_cancel(source)
        finally:
            runtime.backup.lock.release()

        cancel_backup.assert_called_once()
        self.assertIn("已请求取消当前备份", source.replies[-1])

    def test_cancel_prefers_running_restore(self):
        commands, _ = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())
        with mock.patch(
            "mcdr2restic.commands.backup_commands.is_restore_running",
            return_value=True,
        ), mock.patch(
            "mcdr2restic.commands.backup_commands.request_cancel_running_restore"
        ) as cancel_restore, mock.patch(
            "mcdr2restic.commands.backup_commands.request_cancel_current_backup"
        ) as cancel_backup:
            commands.command_cancel(source)

        cancel_restore.assert_called_once()
        cancel_backup.assert_not_called()
        self.assertIn("已请求取消恢复流程", source.replies[-1])

    def test_cancel_during_purge_also_cancels_running_backup(self):
        commands, runtime = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())
        runtime.staging.purge_active = True
        with mock.patch(
            "mcdr2restic.commands.backup_commands.is_backup_running",
            return_value=True,
        ), mock.patch(
            "mcdr2restic.commands.backup_commands.request_cancel_current_backup"
        ) as cancel_backup:
            commands.command_cancel(source)

        self.assertTrue(runtime.staging.copy_slot.cancel.is_set())
        cancel_backup.assert_called_once()
        replies = "\n".join(source.replies)
        self.assertIn("已请求取消中转库 purge", replies)
        self.assertIn("已请求取消当前备份", replies)

    def test_cancel_cancels_pending_deferred_backup(self):
        commands, runtime = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())
        pending = runtime.register_pending_backup("2026-01-01 00:00:00")

        commands.command_cancel(source)

        self.assertTrue(pending.cancel.is_set())
        self.assertIn("已取消推迟中的定时备份", source.replies[-1])

    def test_cancel_without_running_tasks_replies_nothing_running(self):
        commands, _ = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())

        commands.command_cancel(source)

        self.assertIn("没有正在运行", source.replies[-1])


class ReloadCommandTests(unittest.TestCase):
    @staticmethod
    def make_commands():
        from mcdr2restic.commands.backup_commands import BackupCommands
        from mcdr2restic.commands.command_context import CommandContext

        runtime = create_runtime()
        context = CommandContext(
            app_runtime=runtime,
            backup_runner_factory=lambda: None,
            maintenance_runner_factory=lambda: None,
            reload_services=mock.Mock(),
            wake_scheduler=mock.Mock(),
            snapshot_invalidator=mock.Mock(),
        )
        return BackupCommands(context), runtime

    def test_reload_reports_config_error_without_raising(self):
        # 配置损坏等预期错误回复干净消息，不抛给 MCDR 刷 traceback（P2-2）
        commands, _ = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())

        with mock.patch(
            "mcdr2restic.commands.backup_commands.load_config",
            side_effect=ConfigError("error.config.yaml_invalid", path="config.yml"),
        ):
            commands.command_reload(source)

        self.assertIn("YAML 格式损坏", source.replies[-1])
        commands.context.reload_services.assert_not_called()

    def test_reload_success_still_reloads_services(self):
        commands, _ = self.make_commands()
        source = PermissiveCommandSource(FakePluginServer())

        with mock.patch(
            "mcdr2restic.commands.backup_commands.load_config"
        ) as load_config:
            commands.command_reload(source)

        load_config.assert_called_once()
        commands.context.reload_services.assert_called_once()

    def test_reload_warns_when_command_tree_keys_changed(self):
        # 命令树只在插件加载时注册，改了 root 又只说"重载成功"会误导用户（P2-2）
        commands, runtime = self.make_commands()
        runtime.config_state.config = {
            "command": {"root": "!!m2r", "aliases": [], "permission_level": 3}
        }
        source = PermissiveCommandSource(FakePluginServer())

        def fake_load(app_runtime, _server, _source=None):
            app_runtime.config_state.config["command"] = {
                "root": "!!bk",
                "aliases": ["!!backup"],
                "permission_level": 4,
            }

        with mock.patch(
            "mcdr2restic.commands.backup_commands.load_config",
            side_effect=fake_load,
        ):
            commands.command_reload(source)

        self.assertIn("plugin reload", source.replies[-1])

    def test_reload_without_command_changes_keeps_normal_reply(self):
        commands, runtime = self.make_commands()
        runtime.config_state.config = {
            "command": {"root": "!!m2r", "aliases": [], "permission_level": 3}
        }
        source = PermissiveCommandSource(FakePluginServer())

        with mock.patch("mcdr2restic.commands.backup_commands.load_config"):
            commands.command_reload(source)

        self.assertEqual(source.replies, [])


class NotifyTestCommandTests(unittest.TestCase):
    def test_notify_test_lists_channel_results(self):
        from mcdr2restic.commands import notify_commands
        from mcdr2restic.commands.command_context import CommandContext
        from mcdr2restic.commands.notify_commands import NotifyCommands
        from mcdr2restic.notifications.notification_dispatcher import (
            ChannelTestResult,
        )

        runtime = create_runtime()
        context = CommandContext(
            app_runtime=runtime,
            backup_runner_factory=lambda: None,
            maintenance_runner_factory=lambda: None,
            reload_services=mock.Mock(),
            wake_scheduler=mock.Mock(),
            snapshot_invalidator=mock.Mock(),
        )
        source = PermissiveCommandSource(FakePluginServer())
        fake_results = [
            ChannelTestResult(section="onebot", status="sent", error=None),
            ChannelTestResult(
                section="discord", status="failed", error=RuntimeError("boom")
            ),
        ]

        with mock.patch.object(
            notify_commands.NotificationDispatcher,
            "send_test_notification",
            return_value=fake_results,
        ):
            NotifyCommands(context).command_notify_test(source)

        reply = source.replies[-1]
        self.assertIn("通知渠道测试结果", reply)
        self.assertIn("OneBot", reply)
        self.assertIn("已发送", reply)
        self.assertIn("Discord", reply)
        self.assertIn("boom", reply)
