try:
    from .support import create_runtime, unittest
except ImportError:
    from support import create_runtime, unittest


class RuntimeTests(unittest.TestCase):
    def test_grouped_runtime_state_keeps_compatibility_properties(self):
        runtime = create_runtime()
        server = object()
        runtime.config = {"enabled": True}
        runtime.backup_cancel.set()

        runtime.prepare_for_load(server, server_ready=True)

        self.assertIs(runtime.config_state.config, runtime.config)
        self.assertIs(runtime.service.server, server)
        self.assertTrue(runtime.service.server_ready)
        self.assertFalse(runtime.backup.cancel.is_set())
