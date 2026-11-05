try:
    from .support import create_runtime, unittest
except ImportError:
    from support import create_runtime, unittest


class RuntimeTests(unittest.TestCase):
    def test_created_runtimes_have_independent_state(self):
        first = create_runtime()
        second = create_runtime()

        first.service.stopping.set()
        first.backup.cancel.set()
        first.config_state.config = {"enabled": True}

        self.assertFalse(second.service.stopping.is_set())
        self.assertFalse(second.backup.cancel.is_set())
        self.assertEqual(second.config_state.config, {})


if __name__ == "__main__":
    unittest.main()
