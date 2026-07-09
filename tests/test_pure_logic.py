# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import types
import unittest
import os
import io
import json
import tempfile
import threading
from contextlib import closing, nullcontext
from datetime import datetime
from unittest import mock

import yaml


def install_mcdr_stub():
    module_names = [
        'mcdreforged',
        'mcdreforged.api',
        'mcdreforged.api.all',
    ]
    for name in module_names:
        sys.modules.setdefault(name, types.ModuleType(name))
    api_all = sys.modules['mcdreforged.api.all']
    for name in ['PluginServerInterface', 'CommandSource', 'Info', 'Literal', 'Integer', 'Text', 'GreedyText']:
        setattr(api_all, name, type(name, (), {}))
    api_all.__all__ = ['PluginServerInterface', 'CommandSource', 'Info', 'Literal', 'Integer', 'Text', 'GreedyText']


install_mcdr_stub()

from mcdr2restic.minecraft.player_activity import parse_online_list_output, runtime_player_set
from mcdr2restic.minecraft.player_activity_service import has_recent_player_activity, resolve_known_online_players
from mcdr2restic.minecraft.minecraft_service import server_is_running, try_call_bool
from mcdr2restic.notifications import render_message
from mcdr2restic.notifications.discord_webhook import build_discord_mentions, truncate_discord_content
from mcdr2restic.backup.backup_scheduler import BackupScheduler
from mcdr2restic.backup.cron import CronExpression
from mcdr2restic.core.models import BackupProblem, ResticCommandResult, ResticProgressState, RestoreSession
from mcdr2restic.restic.restic_result import detect_error_lines
from mcdr2restic.restic.restic_progress_text import format_restic_status, format_restic_summary
from mcdr2restic.restic.restic_termination import TerminateResult, termination_failure_suffix
from mcdr2restic.restore.restore_workflow import normalize_restore_include_path
import mcdr2restic.restore.restore_workflow as restore_workflow
from mcdr2restic.restore.restore_task_repository import add_restore_task, restore_tasks_output
from mcdr2restic.restore.restore_task_repository import clear_restore_tasks, list_restore_tasks
from mcdr2restic.core.runtime import create_runtime
from mcdr2restic.core.presentation import render_status_output, schedule_status_text
from mcdr2restic.backup.scheduling import parse_daily_time
from mcdr2restic.config.config_loader import replace_or_append_enabled_line
from mcdr2restic.config.config_migration import apply_config_file_migrations, migrate_legacy_config
from mcdr2restic.config.state_store import (
    load_yaml_mapping_with_text_repair,
    repair_inconsistent_block_scalar_indentation,
)
import mcdr2restic.core.bootstrap as bootstrap
from mcdr2restic.core.i18n import make_source_translate, normalize_language, tr, tr_error
from mcdr2restic.defaults.default_config import DEFAULT_CONFIG, build_default_config
from mcdr2restic.defaults.default_config_templates import get_default_config_template
from mcdr2restic.defaults.message_defaults import get_default_message_template
from mcdr2restic.snapshots.snapshot_cache import build_snapshot_cache_key
from mcdr2restic.snapshots.snapshot_db import insert_snapshot_row, open_snapshot_db, read_snapshot_page
from mcdr2restic.snapshots.snapshot_importer import (
    ProcessTimeoutState,
    assert_snapshot_import_finished,
    iter_json_array_stream,
)
import mcdr2restic.restic.restic_service as restic_service
from mcdr2restic.update.update_check import (
    get_current_plugin_version,
    is_newer_version,
    normalize_release_version,
    read_bundled_plugin_version,
    version_number_tuple,
)
from mcdr2restic.core.utils import non_negative_int, safe_int, tail_text


class FakeServer:
    def __init__(self, data_folder):
        self.data_folder = data_folder

    def get_data_folder(self):
        return self.data_folder


class FakeLogger:
    def __init__(self):
        self.debug_messages = []
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    def debug(self, text):
        self.debug_messages.append(str(text))

    def info(self, text):
        self.info_messages.append(str(text))

    def warning(self, text):
        self.warning_messages.append(str(text))

    def error(self, text):
        self.error_messages.append(str(text))


class ProbeServer:
    def __init__(self):
        self.logger = FakeLogger()
        self.startup_calls = 0

    def is_server_startup(self):
        self.startup_calls += 1
        raise RuntimeError('startup probe failed')


class FakePluginServer:
    def __init__(self, language='zh_cn'):
        self.logger = FakeLogger()
        self.language = language

    def get_mcdr_language(self):
        return self.language


class FakeCommandSource:
    def __init__(self, language=''):
        self.language = language
        self.replies = []

    def get_preference(self):
        return types.SimpleNamespace(language=self.language)

    def preferred_language_context(self):
        return nullcontext()

    def reply(self, text):
        self.replies.append(str(text))


class CommandServer(FakePluginServer):
    def __init__(self):
        super().__init__()
        self.commands = []

    def execute(self, command):
        self.commands.append(command)


class PlayerActivityTests(unittest.TestCase):
    def test_parse_english_list_output(self):
        count, names = parse_online_list_output('There are 2 of a max of 20 players online: Steve, Alex')
        self.assertEqual(count, 2)
        self.assertEqual(names, ['Steve', 'Alex'])

    def test_parse_chinese_list_output(self):
        count, names = parse_online_list_output('当前有 3 个玩家在线')
        self.assertEqual(count, 3)
        self.assertEqual(names, [])

    def test_runtime_player_set_defends_against_invalid_shape(self):
        self.assertEqual(runtime_player_set({'known_online_players': object()}), set())

    def test_resolve_known_online_players_prefers_sample_names(self):
        runtime_state = {'known_online_players': ['Steve']}
        self.assertEqual(resolve_known_online_players(runtime_state, 2, ['Alex', 'Steve']), ['Alex', 'Steve'])

    def test_has_recent_player_activity_detects_join(self):
        self.assertTrue(has_recent_player_activity({'player_joined_since_last_check': True}))

    def test_has_recent_player_activity_is_false_when_idle(self):
        self.assertFalse(has_recent_player_activity({'current_online_players': 0}))


class MinecraftServiceTests(unittest.TestCase):
    def test_try_call_bool_logs_probe_failure(self):
        server = ProbeServer()

        result = try_call_bool(server, server.is_server_startup, 'is_server_startup')

        self.assertIsNone(result)
        self.assertIn('is_server_startup', server.logger.debug_messages[0])

    def test_server_is_running_falls_back_to_cached_ready_after_probe_failure(self):
        runtime = create_runtime()
        runtime.service.server_ready = True
        server = ProbeServer()

        self.assertTrue(server_is_running(runtime, server))
        self.assertEqual(server.startup_calls, 1)


class SchedulingTests(unittest.TestCase):
    def test_parse_daily_time(self):
        self.assertEqual(parse_daily_time('07:30'), (7, 30))

    def test_parse_daily_time_fails_fast(self):
        with self.assertRaises(ValueError):
            parse_daily_time('25:00')

    def test_scheduler_loop_triggers_ready_schedule_once(self):
        server = FakePluginServer()
        scheduler = BackupScheduler(
            server,
            lambda: {'enabled': True},
            lambda target, label: True,
            lambda target: True,
            lambda cfg: False,
            lambda key, data, cfg, important: None
        )
        triggered = []

        def trigger():
            triggered.append(True)
            scheduler.stop_event.set()

        scheduler._run_schedule_loop('测试', lambda: (0, 'now'), trigger)

        self.assertEqual(triggered, [True])
        self.assertIn('MCDR2Restic 测试调度线程已启动', server.logger.info_messages)


class CronTests(unittest.TestCase):
    def test_next_after_skips_current_second(self):
        cron = CronExpression('0 0 3 * * *')

        self.assertEqual(
            cron.next_after(datetime(2024, 1, 1, 3, 0, 0)),
            datetime(2024, 1, 2, 3, 0, 0)
        )

    def test_next_after_uses_step_seconds(self):
        cron = CronExpression('*/15 * * * * *')

        self.assertEqual(
            cron.next_after(datetime(2024, 1, 1, 0, 0, 14)),
            datetime(2024, 1, 1, 0, 0, 15)
        )

    def test_next_after_maps_sunday_seven_to_zero(self):
        cron = CronExpression('0 0 0 * * 7')

        self.assertEqual(
            cron.next_after(datetime(2024, 1, 6, 23, 59, 59)),
            datetime(2024, 1, 7, 0, 0, 0)
        )

    def test_cron_error_carries_i18n_key(self):
        with self.assertRaises(Exception) as error:
            CronExpression('* * *')

        self.assertEqual(error.exception.i18n_key, 'error.cron.fields')
        self.assertIn('Cron expression must have 6 fields', tr_error('en_us', error.exception))


class I18nTests(unittest.TestCase):
    def test_make_source_translate_prefers_source_language_over_server_default(self):
        server = FakePluginServer(language='en_us')
        source = FakeCommandSource(language='zh_cn')

        translate = make_source_translate(source, server)

        self.assertEqual(translate('info.backup.enabled'), 'MCDR2Restic 定时备份已启用')

    def test_normalize_language_uses_supported_fallbacks(self):
        self.assertEqual(normalize_language('zh-TW'), 'zh_cn')
        self.assertEqual(normalize_language('fr_fr'), 'en_us')

    def test_translation_formats_named_parameters(self):
        self.assertEqual(
            tr('en_us', 'info.backup.success', label='manual', duration_seconds=3),
            'manual backup completed in 3s'
        )

    def test_translation_accepts_prefixed_mcdr_key(self):
        self.assertEqual(
            tr('en_us', 'mcdr2restic.info.backup.success', label='manual', duration_seconds=3),
            'manual backup completed in 3s'
        )

    def test_translation_keeps_missing_placeholders_visible(self):
        self.assertIn('{level}', tr('zh_cn', 'error.permission.denied'))

    def test_root_lang_files_match_prefixed_package_lang_files(self):
        for name in ('zh_cn', 'en_us'):
            with open(os.path.join('mcdr2restic', 'lang', '{}.json'.format(name)), 'r', encoding='utf8') as file:
                package_lang = json.load(file)
            with open(os.path.join('lang', '{}.json'.format(name)), 'r', encoding='utf8') as file:
                root_lang = json.load(file)

            expected = {'mcdr2restic.{}'.format(key): value for key, value in package_lang.items()}
            self.assertEqual(root_lang, expected)

    def test_default_message_templates_come_from_language_resources(self):
        self.assertIn('Backup started', get_default_message_template('backup_start', 'en_us'))
        self.assertIn('备份开始', get_default_message_template('backup_start', 'zh_cn'))

    def test_default_config_template_renders_placeholders(self):
        for language in ('zh_cn', 'en_us'):
            template = get_default_config_template(language, os.getcwd())

            self.assertIn('messages:', template)
            self.assertNotIn('__MCDR2RESTIC_', template)
            self.assertIsInstance(yaml.safe_load(template), dict)

    def test_load_yaml_mapping_repairs_inconsistent_block_scalar_indentation(self):
        broken_config = (
            'messages:\n'
            '  backup_start: |-\n'
            '        first line\n'
            '    second line\n'
            'config_version: 9\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yml')
            with open(config_path, 'w', encoding='utf8') as file:
                file.write(broken_config)

            load_result = load_yaml_mapping_with_text_repair(
                config_path,
                repair_inconsistent_block_scalar_indentation,
            )

        self.assertEqual(load_result.mapping['messages']['backup_start'], 'first line\nsecond line')
        self.assertIsNotNone(load_result.repaired_text)


class UtilsTests(unittest.TestCase):
    def test_safe_int_uses_default_on_bad_input(self):
        self.assertEqual(safe_int('oops', 7), 7)

    def test_non_negative_int_clamps_negative_values(self):
        self.assertEqual(non_negative_int(-5), 0)

    def test_tail_text_keeps_short_text(self):
        self.assertEqual(tail_text('abc', 10), 'abc')

    def test_schedule_status_text_uses_schedule_helpers(self):
        cfg = {
            'schedule': {'interval_seconds': 60, 'cron_expression': '0'},
            'force_schedule': {'interval_seconds': 0, 'cron_expression': '0'},
        }

        self.assertEqual(
            schedule_status_text(cfg, False, 'zh_cn'),
            '60 秒后（固定间隔 60 秒）'
        )
        self.assertEqual(
            schedule_status_text(cfg, True, 'zh_cn'),
            '关闭'
        )

    def test_render_status_output_uses_source_translate(self):
        source = FakeCommandSource(language='zh_cn')
        server = FakePluginServer(language='en_us')
        cfg = {
            'enabled': True,
            'runtime': {
                'current_online_players': 0,
                'last_backup_status': 'never',
            },
            'schedule': {'interval_seconds': 60, 'cron_expression': '0'},
            'force_schedule': {'interval_seconds': 0, 'cron_expression': '0'},
            'snapshot_cache': {'enabled': False},
        }

        output = render_status_output(
            threading.Lock(),
            cfg,
            'zh_cn',
            server,
            1,
            backup_running_provider=lambda: False,
            restore_running_provider=lambda: False,
            mc_ready_provider=lambda _: True,
            translate=make_source_translate(source, server),
        )

        self.assertIn('MCDR2Restic 状态', output)
        self.assertIn('正常备份: 60 秒后（固定间隔 60 秒）', output)


class UpdateCheckTests(unittest.TestCase):
    def test_normalize_release_version(self):
        self.assertEqual(normalize_release_version('Version v1.2.3'), '1.2.3')

    def test_version_number_tuple_ignores_suffix(self):
        self.assertEqual(version_number_tuple('v1.2.3-beta'), (1, 2, 3))

    def test_is_newer_version_pads_missing_parts(self):
        self.assertTrue(is_newer_version('1.2.1', '1.2'))
        self.assertFalse(is_newer_version('1.2.0', '1.2'))

    def test_bundled_plugin_version_reads_repository_metadata(self):
        with open('mcdreforged.plugin.json', 'r', encoding='utf8') as file:
            metadata = json.load(file)

        self.assertEqual(read_bundled_plugin_version(), metadata['version'])

    def test_current_plugin_version_falls_back_to_bundled_metadata(self):
        self.assertEqual(get_current_plugin_version(None), read_bundled_plugin_version())


class NotificationTests(unittest.TestCase):
    def test_render_message_keeps_unknown_placeholders(self):
        cfg = {
            'onebot': {'message_prefix': '[T]'},
            'messages': {'custom': '{prefix} {name} {missing}'},
        }
        self.assertEqual(render_message('custom', {'name': 'ok'}, cfg), '[T] ok {missing}')

    def test_discord_mentions_are_explicit_and_ordered(self):
        cfg = {
            'mention_everyone': True,
            'mention_role_ids': [' 1 ', ''],
            'mention_user_ids': ['2'],
        }

        self.assertEqual(build_discord_mentions(cfg), ['@everyone', '<@&1>', '<@2>'])

    def test_discord_content_truncates_to_webhook_limit(self):
        self.assertEqual(len(truncate_discord_content('x' * 2100)), 2000)


class ResticResultTests(unittest.TestCase):
    def test_detect_error_lines_honors_ignore_patterns(self):
        lines = detect_error_lines(
            'ok\nerror: failed\nerror: ignored\n',
            [r'error:'],
            [r'ignored']
        )
        self.assertEqual(lines, ['error: failed'])


class BackupFlowTests(unittest.TestCase):
    def test_run_backup_body_executes_maintenance_then_minecraft_then_backup(self):
        runtime = create_runtime()
        runtime.service.server_ready = True
        server = CommandServer()
        cfg = {
            'minecraft': {
                'save_off_command': 'save-off',
                'save_all_command': 'save-all',
                'wait_after_save_off_seconds': 0,
                'wait_after_save_all_seconds': 0,
            },
            'restic': {
                'maintenance_commands': [['forget']],
                'backup_command': ['backup', 'world'],
                'timeout_seconds': 0,
            }
        }
        restic_calls = []

        def fake_run_restic(_runtime, _restic_cfg, args, phase, _deadline):
            restic_calls.append((phase, list(args)))
            return ResticCommandResult(phase, list(args), 0, '', '', 0, snapshot_id='abc123')

        with mock.patch.object(restic_service, 'is_mc_ready', return_value=True), \
                mock.patch.object(restic_service, 'assert_backup_sources_do_not_contain_repository'), \
                mock.patch.object(restic_service, 'ensure_default_restic_executable_available'), \
                mock.patch.object(restic_service, 'ensure_restic_repository_initialized', return_value=False), \
                mock.patch.object(restic_service, 'run_restic_command', side_effect=fake_run_restic), \
                mock.patch.object(restic_service, 'assert_restic_success'):
            snapshot_id = restic_service.run_backup_body(runtime, server, cfg, 'manual', lambda *_args: None)

        self.assertEqual(snapshot_id, 'abc123')
        self.assertEqual(restic_calls, [('maintenance', ['forget']), ('backup', ['backup', 'world'])])
        self.assertEqual(server.commands, ['save-off', 'save-all'])


class ResticProgressTests(unittest.TestCase):
    def test_format_status_uses_progress_values(self):
        progress = ResticProgressState(
            phase='backup',
            language='en_us',
            status={
                'percent_done': 0.5,
                'files_done': 1,
                'total_files': 2,
                'bytes_done': 1024,
                'total_bytes': 2048,
            }
        )

        text = format_restic_status(progress)

        self.assertIn('50.0%', text)
        self.assertIn('files 1/2', text)
        self.assertIn('1.0 KiB/2.0 KiB', text)

    def test_format_summary_reports_snapshot_id(self):
        progress = ResticProgressState(
            phase='backup',
            language='en_us',
            summary={'snapshot_id': 'abcdef123456', 'total_files_processed': 3}
        )

        self.assertIn('snapshot abcdef12', format_restic_summary(progress))


class ResticTerminationTests(unittest.TestCase):
    def test_terminate_result_reports_successful_paths(self):
        self.assertTrue(TerminateResult(graceful=True).terminated)
        self.assertTrue(TerminateResult(killed=True).terminated)
        self.assertFalse(TerminateResult(error='failed').terminated)

    def test_termination_failure_suffix_reports_error(self):
        self.assertEqual(termination_failure_suffix(TerminateResult(graceful=True)), '')
        self.assertIn('kill failed', termination_failure_suffix(TerminateResult(error='kill failed')))


class RestoreWorkflowTests(unittest.TestCase):
    def test_normalize_restore_include_path_returns_restic_absolute_path(self):
        restic_cfg = {'working_directory': os.getcwd()}
        self.assertEqual(
            normalize_restore_include_path('world/region', restic_cfg, '!!backup'),
            '/world/region'
        )

    def test_restore_startup_finishes_session_and_releases_lock(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.restore.lock.acquire()
        runtime.restore.session = RestoreSession(
            tasks=[],
            cfg={},
            snapshot_cfg={},
            cache_key='cache',
            language='zh_cn',
            phase='starting',
            started_at='now'
        )

        restore_workflow.handle_restore_server_startup(runtime, server)

        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn('恢复流程完成', server.logger.info_messages[0])

    def test_restore_startup_timeout_finishes_session_and_releases_lock(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.restore.lock.acquire()
        runtime.restore.session = RestoreSession([], {'restore': {}}, {}, 'cache', 'zh_cn', 'starting', 'now')

        finished = restore_workflow.finish_restore_start_timeout_if_still_starting(
            runtime,
            server,
            restore_workflow.RestoreStageResult(),
            1
        )

        self.assertTrue(finished)
        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn('未完成', server.logger.warning_messages[0])

    def test_restore_startup_timeout_ignores_newer_session(self):
        runtime = create_runtime()
        server = FakePluginServer()
        old_session = RestoreSession([], {'restore': {}}, {}, 'cache-old', 'zh_cn', 'starting', 'old')
        new_session = RestoreSession([], {'restore': {}}, {}, 'cache-new', 'zh_cn', 'starting', 'new')
        runtime.restore.lock.acquire()
        runtime.restore.session = new_session

        finished = restore_workflow.finish_restore_start_timeout_if_still_starting(
            runtime,
            server,
            restore_workflow.RestoreStageResult(),
            1,
            expected_session=old_session
        )

        self.assertFalse(finished)
        self.assertIs(runtime.restore.session, new_session)
        self.assertTrue(runtime.restore.lock.locked())
        self.assertEqual(server.logger.warning_messages, [])

    def test_restore_server_stop_during_starting_finishes_session(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.restore.lock.acquire()
        runtime.restore.session = RestoreSession([], {}, {}, 'cache', 'zh_cn', 'starting', 'now')

        handled = restore_workflow.handle_restore_server_stop(runtime, server, 1, clear_restore_tasks)

        self.assertTrue(handled)
        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn('再次停止', server.logger.warning_messages[0])


class ConfigurationTests(unittest.TestCase):
    def test_default_config_constructor_returns_independent_copies(self):
        first = build_default_config()
        second = build_default_config()

        first['restic']['backup_command'].append('--changed')

        self.assertNotIn('--changed', second['restic']['backup_command'])
        with self.assertRaises(TypeError):
            DEFAULT_CONFIG['enabled'] = False
        with self.assertRaises(TypeError):
            DEFAULT_CONFIG['restic']['password'] = 'changed'

    def test_migrate_legacy_config_moves_restic_environment_secrets(self):
        cfg = {
            'config_version': 1,
            'restic': {
                'environment': {
                    'RESTIC_REPOSITORY': '/repo',
                    'RESTIC_PASSWORD_FILE': 'password.txt',
                },
                'timeout_seconds': 3600,
            },
            'schedule': {'require_player_joined_in_wait_period': False},
        }

        migrate_legacy_config(cfg)

        self.assertEqual(cfg['restic']['repository'], '/repo')
        self.assertEqual(cfg['restic']['password_file'], 'password.txt')
        self.assertEqual(cfg['restic']['password'], '')
        self.assertEqual(cfg['restic']['timeout_seconds'], 0)
        self.assertFalse(cfg['schedule']['require_player_activity_in_wait_period'])

    def test_apply_config_file_migrations_removes_deprecated_schedule_keys(self):
        lines = [
            'enabled: true\n',
            'schedule:\n',
            '  # nobody joined during waiting period\n',
            '  require_player_joined_in_wait_period: true\n',
            '  online_check_interval_seconds: 60\n',
            'restic:\n',
            '  executable: restic\n',
            'messages:\n',
            '  ok: ok\n',
        ]
        cfg = {
            'schedule': {'require_player_activity_in_wait_period': True, 'online_check_command': 'list'},
            'restic': {},
        }

        migrated = ''.join(apply_config_file_migrations(lines, 'en_us', cfg))

        self.assertIn('require_player_activity_in_wait_period', migrated)
        self.assertIn('online_check_command', migrated)
        self.assertNotIn('  require_player_joined_in_wait_period:', migrated)
        self.assertNotIn('  online_check_interval_seconds:', migrated)
        self.assertIn('config_version:', migrated)

    def test_apply_config_file_migrations_is_idempotent(self):
        lines = [
            'enabled: true\n',
            'schedule:\n',
            '  interval_seconds: 0\n',
            'restic:\n',
            '  executable: restic\n',
            'minecraft:\n',
            '  save_off_command: save-off\n',
            'notification:\n',
            '  notify_on_success: true\n',
            'messages:\n',
            '  ok: ok\n',
        ]
        cfg = build_default_config('en_us')

        first = apply_config_file_migrations(lines, 'en_us', cfg)
        second = apply_config_file_migrations(first, 'en_us', cfg)

        self.assertEqual(first, second)
        migrated = ''.join(first)
        self.assertEqual(migrated.count('force_schedule:'), 1)
        self.assertEqual(migrated.count('update_check:'), 1)
        self.assertEqual(migrated.count('snapshot_cache:'), 1)
        self.assertEqual(migrated.count('restore:'), 1)

    def test_apply_config_file_migrations_keeps_top_level_comments_with_following_block(self):
        lines = [
            'enabled: true\n',
            'schedule:\n',
            '  interval_seconds: 0\n',
            '\n',
            '# Restic settings stay attached\n',
            'restic:\n',
            '  executable: restic\n',
            '\n',
            '# Minecraft commands stay attached\n',
            'minecraft:\n',
            '  save_off_command: save-off\n',
            'notification:\n',
            '  notify_on_success: true\n',
            'messages:\n',
            '  ok: ok\n',
        ]
        cfg = build_default_config('en_us')

        migrated = apply_config_file_migrations(lines, 'en_us', cfg)

        restic_comment_index = migrated.index('# Restic settings stay attached\n')
        minecraft_comment_index = migrated.index('# Minecraft commands stay attached\n')
        self.assertEqual(migrated[restic_comment_index + 1], 'restic:\n')
        self.assertEqual(migrated[minecraft_comment_index + 1], 'minecraft:\n')

    def test_replace_or_append_enabled_line(self):
        self.assertEqual(
            replace_or_append_enabled_line(['name: test\n'], False),
            ['name: test\n', 'enabled: false\n']
        )
        self.assertEqual(
            replace_or_append_enabled_line(['enabled: true\n'], False),
            ['enabled: false\n']
        )


class BootstrapTests(unittest.TestCase):
    def test_requirements_txt_declares_runtime_dependencies(self):
        with open('requirements.txt', 'r', encoding='utf8') as file:
            requirements = set(line.strip() for line in file if line.strip())

        self.assertIn('PyYAML>=6.0', requirements)
        self.assertIn('websocket-client>=1.8.0', requirements)

    def test_bootstrap_no_longer_exposes_pip_installer(self):
        self.assertFalse(hasattr(bootstrap, 'pip_install'))
        self.assertFalse(hasattr(bootstrap, 'run_pip_command'))


class RuntimeTests(unittest.TestCase):
    def test_grouped_runtime_state_keeps_compatibility_properties(self):
        runtime = create_runtime()
        server = object()
        runtime.config = {'enabled': True}
        runtime.backup_cancel.set()

        runtime.prepare_for_load(server, server_ready=True)

        self.assertIs(runtime.config_state.config, runtime.config)
        self.assertIs(runtime.service.server, server)
        self.assertTrue(runtime.service.server_ready)
        self.assertFalse(runtime.backup.cancel.is_set())


class SnapshotCacheTests(unittest.TestCase):
    def test_cache_key_hashes_secrets_and_is_stable(self):
        cfg = {
            'repository': '/repo',
            'password': 'secret-password',
            'environment': {'TOKEN': 'abc', 'EMPTY': None},
        }
        same_cfg_different_order = {
            'password': 'secret-password',
            'environment': {'EMPTY': None, 'TOKEN': 'abc'},
            'repository': '/repo',
        }

        cache_key = build_snapshot_cache_key(cfg)

        self.assertEqual(cache_key, build_snapshot_cache_key(same_cfg_different_order))
        self.assertNotIn('secret-password', cache_key)
        self.assertNotIn('abc', cache_key)
        self.assertEqual(len(cache_key), 64)


class SnapshotImporterTests(unittest.TestCase):
    def test_iter_json_array_stream_reads_snapshot_objects(self):
        stream = io.StringIO('[{"id":"a"}, {"id":"b", "paths":["world"]}]')

        self.assertEqual(
            list(iter_json_array_stream(stream)),
            [{'id': 'a'}, {'id': 'b', 'paths': ['world']}]
        )

    def test_timeout_error_includes_termination_failure(self):
        timeout_state = ProcessTimeoutState(threading.Event(), TerminateResult(error='kill failed'))
        timeout_state.timed_out.set()

        with self.assertRaises(BackupProblem) as error:
            assert_snapshot_import_finished(3, timeout_state, 0, '')

        self.assertIn('终止失败', str(error.exception))
        self.assertIn('kill failed', str(error.exception))


class SnapshotDatabaseTests(unittest.TestCase):
    def test_read_snapshot_page_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {'database': 'snapshots.sqlite3'}
            with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
                insert_snapshot_row(conn, 'cache', {'id': 'old', 'time': '2024-01-01T00:00:00Z'})
                insert_snapshot_row(conn, 'cache', {'id': 'new', 'time': '2024-01-02T00:00:00Z'})
                conn.commit()

            page = read_snapshot_page(server, 'cache', page=1, page_size=1, snapshot_cfg=snapshot_cfg)

            self.assertEqual(page['total'], 2)
            self.assertEqual(page['rows'][0]['id'], 'new')

    def test_restore_tasks_output_uses_repository_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {'database': 'snapshots.sqlite3'}

            task_id = add_restore_task(server, snapshot_cfg, 'cache-a', 'abcdef12', 'file', '/world/level.dat')
            output = restore_tasks_output(server, snapshot_cfg, 'cache-a', 'en_us', '!!restic')

            self.assertIn('MCDR2Restic Restore Tasks', output)
            self.assertIn('{}. [file] abcdef12 -> /world/level.dat'.format(task_id), output)

    def test_restore_tasks_output_accepts_source_translate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {'database': 'snapshots.sqlite3'}
            source = FakeCommandSource(language='zh_cn')
            plugin_server = FakePluginServer(language='en_us')

            task_id = add_restore_task(server, snapshot_cfg, 'cache-a', 'abcdef12', 'file', '/world/level.dat')
            output = restore_tasks_output(
                server,
                snapshot_cfg,
                'cache-a',
                make_source_translate(source, plugin_server),
                '!!restic',
            )

            self.assertIn('MCDR2Restic 恢复任务列表', output)
            self.assertIn('{}. [文件] abcdef12 -> /world/level.dat'.format(task_id), output)

    def test_restore_tasks_are_isolated_by_cache_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            snapshot_cfg = {'database': 'snapshots.sqlite3'}
            add_restore_task(server, snapshot_cfg, 'cache-a', 'a', 'full', '/')
            add_restore_task(server, snapshot_cfg, 'cache-b', 'b', 'full', '/')

            self.assertEqual([row['snapshot'] for row in list_restore_tasks(server, snapshot_cfg, 'cache-a')], ['a'])
            self.assertEqual(clear_restore_tasks(server, snapshot_cfg, 'cache-a'), 1)
            self.assertEqual([row['snapshot'] for row in list_restore_tasks(server, snapshot_cfg, 'cache-b')], ['b'])


if __name__ == '__main__':
    unittest.main()
