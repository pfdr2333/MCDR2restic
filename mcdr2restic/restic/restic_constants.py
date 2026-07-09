# -*- coding: utf-8 -*-
from __future__ import annotations


RESTIC_ENV_REPOSITORY = 'RESTIC_REPOSITORY'
RESTIC_ENV_PASSWORD = 'RESTIC_PASSWORD'
RESTIC_ENV_PASSWORD_FILE = 'RESTIC_PASSWORD_FILE'
RESTIC_ENV_PASSWORD_COMMAND = 'RESTIC_PASSWORD_COMMAND'

RESTIC_CFG_AUTO_INIT_LOCAL_REPOSITORY = 'auto_init_local_repository'
RESTIC_CFG_AUTO_DOWNLOAD = 'auto_download'
RESTIC_CFG_BACKUP_COMMAND = 'backup_command'
RESTIC_CFG_DOWNLOAD_PROXY_PREFIXES = 'download_proxy_prefixes'
RESTIC_CFG_DOWNLOAD_TIMEOUT_SECONDS = 'download_timeout_seconds'
RESTIC_CFG_DOWNLOAD_VERSION = 'download_version'
RESTIC_CFG_ENVIRONMENT = 'environment'
RESTIC_CFG_ERROR_REGEXES = 'error_regexes'
RESTIC_CFG_EXECUTABLE = 'executable'
RESTIC_CFG_IGNORE_ERROR_REGEXES = 'ignore_error_regexes'
RESTIC_CFG_MAINTENANCE_COMMANDS = 'maintenance_commands'
RESTIC_CFG_MAX_OUTPUT_CHARS = 'max_output_chars_in_notification'
RESTIC_CFG_PASSWORD = 'password'
RESTIC_CFG_PASSWORD_FILE = 'password_file'
RESTIC_CFG_PROGRESS_INTERVAL = 'progress_interval_seconds'
RESTIC_CFG_REPOSITORY = 'repository'
RESTIC_CFG_SUCCESS_EXIT_CODES = 'success_exit_codes'
RESTIC_CFG_TIMEOUT_SECONDS = 'timeout_seconds'
RESTIC_CFG_WORKING_DIRECTORY = 'working_directory'

RESTIC_COMMAND_BACKUP = 'backup'
RESTIC_COMMAND_INIT = 'init'
RESTIC_COMMAND_RESTORE = 'restore'
RESTIC_COMMAND_ROLLBACK = 'rollback'
RESTIC_COMMAND_SNAPSHOTS = 'snapshots'
RESTIC_PHASE_MAINTENANCE = 'maintenance'

RESTIC_OPTION_INCLUDE = '--include'
RESTIC_OPTION_JSON = '--json'
RESTIC_OPTION_REPOSITORY = '--repository'
RESTIC_OPTION_REPOSITORY_SHORT = '-r'
RESTIC_OPTION_REPO = '--repo'
RESTIC_OPTION_TAG = '--tag'
RESTIC_OPTION_TARGET = '--target'

RESTIC_REPOSITORY_OPTIONS = frozenset({
    RESTIC_OPTION_REPO,
    RESTIC_OPTION_REPOSITORY,
    RESTIC_OPTION_REPOSITORY_SHORT,
})

RESTIC_BACKUP_VALUE_OPTIONS = frozenset({
    '--exclude',
    '--iexclude',
    '--exclude-file',
    '--iexclude-file',
    '--exclude-if-present',
    '--exclude-larger-than',
    '--files-from',
    '--files-from-raw',
    '--files-from-verbatim',
    '--group-by',
    '--host',
    '--limit-download',
    '--limit-upload',
    '--option',
    '--parent',
    '--password-command',
    '--password-file',
    RESTIC_OPTION_REPO,
    RESTIC_OPTION_REPOSITORY,
    '--repository-file',
    '--stdin-filename',
    RESTIC_OPTION_TAG,
    '--time',
})

RESTIC_JSON_OUTPUT_PHASES = frozenset({
    RESTIC_COMMAND_BACKUP,
    RESTIC_COMMAND_RESTORE,
    RESTIC_COMMAND_ROLLBACK,
})
