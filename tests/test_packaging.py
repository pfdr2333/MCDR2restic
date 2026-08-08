try:
    from .support import Path, bootstrap, json, pack_plugin, tempfile, unittest, zipfile
except ImportError:
    from support import Path, bootstrap, json, pack_plugin, tempfile, unittest, zipfile
class BootstrapTests(unittest.TestCase):
    def test_requirements_txt_declares_runtime_dependencies(self):
        with open("requirements.txt", "r", encoding="utf8") as file:
            requirements = set(line.strip() for line in file if line.strip())

        self.assertIn("PyYAML>=6.0", requirements)
        self.assertIn("websocket-client>=1.8.0", requirements)

    def test_bootstrap_no_longer_exposes_pip_installer(self):
        self.assertFalse(hasattr(bootstrap, "pip_install"))
        self.assertFalse(hasattr(bootstrap, "run_pip_command"))

class PackagingTests(unittest.TestCase):
    def test_metadata_declares_root_resources_for_packaging(self):
        with open("mcdreforged.plugin.json", "r", encoding="utf8") as file:
            metadata = json.load(file)

        self.assertIn("lang", metadata.get("resources", []))
        self.assertIn("config_templates", metadata.get("resources", []))

    def test_pack_plugin_builds_posix_archive_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = pack_plugin.pack_plugin(Path("."), Path(temp_dir))

            with zipfile.ZipFile(archive_path, "r") as archive:
                names = archive.namelist()

        self.assertEqual(archive_path.name, "MCDR2Restic_v0.5.0.mcdr")
        self.assertIn("mcdr2restic/__init__.py", names)
        self.assertIn("lang/zh_cn.json", names)
        self.assertIn("lang/zh_tw.json", names)
        self.assertIn("config_templates/zh_cn.yml", names)
        self.assertIn("config_templates/zh_tw.yml", names)
        self.assertIn("mcdreforged.plugin.json", names)
        self.assertTrue(all("\\" not in name for name in names))
        self.assertFalse(any("__pycache__" in name for name in names))
