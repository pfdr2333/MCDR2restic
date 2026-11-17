# -*- coding: utf-8 -*-
"""真实 restic 的中转库端到端校验。

默认跳过；设置 ``MCDR2RESTIC_RESTIC_BINARY`` 为 restic 可执行文件路径后运行：

    set MCDR2RESTIC_RESTIC_BINARY=C:\\path\\to\\restic.exe
    python -m unittest tests.test_staging_restic_integration -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from .support import FakePluginServer, create_runtime, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, unittest

from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.staging.staging_backup import run_local_backup
from mcdr2restic.staging.staging_config import (
    build_remote_restic_config,
    get_staging_settings,
    resolve_staging_paths,
)
from mcdr2restic.staging.staging_copy import run_copy_cycle
from mcdr2restic.staging.staging_purge import purge_staging_remote
from mcdr2restic.staging.staging_repository import (
    assert_matching_chunkers,
    ensure_local_repository,
)


RESTIC_BINARY = os.environ.get("MCDR2RESTIC_RESTIC_BINARY", "")


@unittest.skipUnless(
    RESTIC_BINARY and os.path.isfile(RESTIC_BINARY),
    "set MCDR2RESTIC_RESTIC_BINARY to run real-restic staging integration",
)
class StagingResticIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="m2r_staging_it_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = self.root / "world"
        self.source.mkdir()
        self.local_repo = self.root / "staging-repo"
        self.remote_repo = self.root / "remote-repo"
        self.remote_password = "remote-pass"
        self.local_password = "local-pass"
        self.env = os.environ.copy()
        self.env["RESTIC_PASSWORD"] = self.remote_password
        self.run_restic("-r", str(self.remote_repo), "init")

        self.server = FakePluginServer()
        # 状态落盘必须进临时目录，不能写仓库根目录的 state.yml
        self.server.get_data_folder = lambda: str(self.root)
        self.runtime = create_runtime()
        cfg = build_default_config()
        cfg["runtime"] = build_default_runtime()
        cfg["staging"].update(
            {
                "enabled": True,
                "repository": str(self.local_repo),
                "password": self.local_password,
                "password_file": "",
                "cache_dir": str(self.root / "cache"),
                "min_free_percent_after": 1,
            }
        )
        cfg["restic"].update(
            {
                "executable": RESTIC_BINARY,
                "working_directory": str(self.root),
                "repository": str(self.remote_repo),
                "password": self.remote_password,
                "password_file": "",
                "backup_command": ["backup", str(self.source), "--tag", "it"],
                "maintenance_commands": [],
            }
        )
        self.cfg = cfg
        self.runtime.config_state.config = cfg
        self.settings = get_staging_settings(cfg)
        self.paths = resolve_staging_paths(self.settings, cfg["restic"])
        self.local_cfg = ensure_local_repository(
            self.runtime,
            self.server,
            cfg["restic"],
            self.settings,
            self.paths,
        )

    def run_restic(self, *args):
        return subprocess.run(
            [RESTIC_BINARY, *args],
            check=True,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def remote_snapshots(self):
        result = self.run_restic(
            "-r", str(self.remote_repo), "snapshots", "--json"
        )
        return json.loads(result.stdout)

    def test_copy_cursor_and_purge_round_trip(self):
        (self.source / "level.dat").write_text("v1 " * 4000, encoding="utf8")
        local_id = run_local_backup(
            self.runtime,
            self.server,
            self.cfg,
            self.local_cfg,
            self.settings,
            self.paths,
            None,
        )

        first = run_copy_cycle(
            self.runtime, self.server, self.cfg, self.settings, self.paths
        )
        self.assertTrue(first.success)
        self.assertEqual(first.copied_count, 1)
        remote = self.remote_snapshots()
        self.assertEqual(len(remote), 1)
        self.assertEqual(remote[0]["original"], local_id)

        second = run_copy_cycle(
            self.runtime, self.server, self.cfg, self.settings, self.paths
        )
        self.assertTrue(second.success)
        self.assertEqual(second.copied_count, 0)

        (self.source / "level.dat").write_text("v2 " * 4500, encoding="utf8")
        run_local_backup(
            self.runtime,
            self.server,
            self.cfg,
            self.local_cfg,
            self.settings,
            self.paths,
            None,
        )
        third = run_copy_cycle(
            self.runtime, self.server, self.cfg, self.settings, self.paths
        )
        self.assertTrue(third.success)
        self.assertEqual(third.copied_count, 1)
        self.assertEqual(len(self.remote_snapshots()), 2)

        message = purge_staging_remote(
            self.runtime, self.server, self.cfg, force=False
        )
        self.assertIn(".purged-", message)
        assert_matching_chunkers(
            self.runtime,
            self.local_cfg,
            build_remote_restic_config(self.cfg["restic"]),
        )
        after_purge = run_copy_cycle(
            self.runtime, self.server, self.cfg, self.settings, self.paths
        )
        self.assertTrue(after_purge.success)
        self.assertEqual(after_purge.copied_count, 2)
        self.assertEqual(len(self.remote_snapshots()), 2)


if __name__ == "__main__":
    unittest.main()
