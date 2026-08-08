try:
    from .support import command_handlers, mock, unittest
except ImportError:
    from support import command_handlers, mock, unittest


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
