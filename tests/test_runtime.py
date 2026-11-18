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

    def test_acquire_backup_slot_waiting_gives_up_when_stopping(self):
        runtime = create_runtime()
        runtime.backup.lock.acquire()
        runtime.service.stopping.set()

        try:
            self.assertFalse(runtime.acquire_backup_slot_waiting())
        finally:
            runtime.backup.lock.release()

    def test_acquire_backup_slot_waiting_respects_give_up_predicate(self):
        runtime = create_runtime()
        runtime.backup.lock.acquire()
        runtime.backup.label = "manual"

        try:
            self.assertFalse(
                runtime.acquire_backup_slot_waiting(
                    lambda: runtime.backup.label != "maintenance",
                    interval=0.01,
                )
            )
        finally:
            runtime.backup.lock.release()


if __name__ == "__main__":
    unittest.main()
