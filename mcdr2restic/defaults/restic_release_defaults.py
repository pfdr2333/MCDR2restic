# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Any, Dict

from mcdr2restic.defaults.default_freeze import freeze_default


_RESTIC_FALLBACK_RELEASE: Dict[str, Any] = {
    'tag_name': 'v0.19.1',
    'assets': [
        {
            'name': 'restic_0.19.1_linux_amd64.bz2',
            'browser_download_url': 'https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_linux_amd64.bz2',
        },
        {
            'name': 'restic_0.19.1_windows_amd64.zip',
            'browser_download_url': 'https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_windows_amd64.zip',
        },
    ],
}

RESTIC_FALLBACK_RELEASE = freeze_default(_RESTIC_FALLBACK_RELEASE)


def build_restic_fallback_release() -> Dict[str, Any]:
    return copy.deepcopy(_RESTIC_FALLBACK_RELEASE)
