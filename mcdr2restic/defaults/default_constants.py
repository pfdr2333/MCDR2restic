# -*- coding: utf-8 -*-
from __future__ import annotations


PLUGIN_ID = 'mcdr2restic'

CONFIG_NAME = 'config.yml'

STATE_NAME = 'state.yml'

SNAPSHOT_DB_NAME = 'snapshots.sqlite3'

LEGACY_CONFIG_NAME = 'config.json'

CONFIG_VERSION = 9

PLUGIN_REPOSITORY_URL = 'https://github.com/pfdr2333/MCDR2restic'

DEFAULT_UPDATE_API_URL = 'https://api.github.com/repos/pfdr2333/MCDR2restic/releases/latest'

DEFAULT_PROXY_PREFIXES = (
    'https://gh.llkk.cc/',
    'https://gh-proxy.com/',
    'https://hub.gitmirror.com/',
)

SNAPSHOT_PAGE_SIZE = 10

SNAPSHOT_QUERY_TIMEOUT_SECONDS = 30

RESTIC_PROGRESS_INTERVAL_SECONDS = 5
