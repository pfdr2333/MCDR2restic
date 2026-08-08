try:
    from .support import (
        get_current_plugin_version,
        is_newer_version,
        json,
        normalize_release_version,
        read_bundled_plugin_version,
        unittest,
        version_number_tuple,
    )
except ImportError:
    from support import (
        get_current_plugin_version,
        is_newer_version,
        json,
        normalize_release_version,
        read_bundled_plugin_version,
        unittest,
        version_number_tuple,
    )
class UpdateCheckTests(unittest.TestCase):
    def test_normalize_release_version(self):
        self.assertEqual(normalize_release_version("Version v1.2.3"), "1.2.3")

    def test_version_number_tuple_ignores_suffix(self):
        self.assertEqual(version_number_tuple("v1.2.3-beta"), (1, 2, 3))

    def test_is_newer_version_pads_missing_parts(self):
        self.assertTrue(is_newer_version("1.2.1", "1.2"))
        self.assertFalse(is_newer_version("1.2.0", "1.2"))

    def test_bundled_plugin_version_reads_repository_metadata(self):
        with open("mcdreforged.plugin.json", "r", encoding="utf8") as file:
            metadata = json.load(file)

        self.assertEqual(read_bundled_plugin_version(), metadata["version"])

    def test_current_plugin_version_falls_back_to_bundled_metadata(self):
        self.assertEqual(
            get_current_plugin_version(None), read_bundled_plugin_version()
        )
