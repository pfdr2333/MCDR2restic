# -*- coding: utf-8 -*-
try:
    from .support import (
        FakePluginServer,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )

from mcdr2restic.core.bootstrap import BootstrapResult
from mcdr2restic.core.plugin import PluginEntrypoint


def staging_config(enabled):
    cfg = build_default_config()
    cfg["staging"].update(
        {"enabled": enabled, "repository": "/data/staging", "password": "pass"}
    )
    return cfg


class StagingWorkerLifecycleTest(unittest.TestCase):
    def make_entrypoint(self, enabled):
        runtime = create_runtime()
        runtime.config_state.config = staging_config(enabled)
        return runtime, PluginEntrypoint(runtime, BootstrapResult(logs=[]))

    def test_disabled_does_not_create_or_start_worker(self):
        runtime, entrypoint = self.make_entrypoint(False)

        with mock.patch(
            "mcdr2restic.core.plugin.StagingCopyWorker"
        ) as worker_class:
            entrypoint.start_staging_worker(FakePluginServer())

        worker_class.assert_not_called()
        self.assertIsNone(runtime.staging.worker)

    def test_enabled_starts_worker_and_stop_cleans_up(self):
        runtime, entrypoint = self.make_entrypoint(True)
        worker = mock.Mock()

        with mock.patch(
            "mcdr2restic.core.plugin.StagingCopyWorker", return_value=worker
        ) as worker_class:
            entrypoint.start_staging_worker(FakePluginServer())

        worker_class.assert_called_once()
        worker.start.assert_called_once()
        self.assertIs(runtime.staging.worker, worker)

        entrypoint.stop_staging_worker()

        worker.stop.assert_called_once()
        self.assertIsNone(runtime.staging.worker)

    def test_reload_restarts_worker(self):
        runtime, entrypoint = self.make_entrypoint(True)
        first = mock.Mock()
        second = mock.Mock()

        with mock.patch(
            "mcdr2restic.core.plugin.StagingCopyWorker",
            side_effect=[first, second],
        ):
            entrypoint.start_staging_worker(FakePluginServer())
            entrypoint.start_staging_worker(FakePluginServer())

        first.stop.assert_called_once()
        second.start.assert_called_once()
        self.assertIs(runtime.staging.worker, second)


if __name__ == "__main__":
    unittest.main()
