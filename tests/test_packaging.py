try:
    from .support import (
        REPO_ROOT,
        Path,
        bootstrap,
        json,
        pack_plugin,
        tempfile,
        unittest,
        verify_plugin,
        zipfile,
    )
except ImportError:
    from support import (
        REPO_ROOT,
        Path,
        bootstrap,
        json,
        pack_plugin,
        tempfile,
        unittest,
        verify_plugin,
        zipfile,
    )


class BootstrapTests(unittest.TestCase):
    def test_requirements_txt_declares_runtime_dependencies(self):
        with open(REPO_ROOT / "requirements.txt", "r", encoding="utf8") as file:
            requirements = set(line.strip() for line in file if line.strip())

        self.assertIn("PyYAML>=6.0", requirements)
        self.assertIn("websocket-client>=1.8.0", requirements)

    def test_bootstrap_no_longer_exposes_pip_installer(self):
        self.assertFalse(hasattr(bootstrap, "pip_install"))
        self.assertFalse(hasattr(bootstrap, "run_pip_command"))


class PackagingTests(unittest.TestCase):
    def test_metadata_declares_root_resources_for_packaging(self):
        with open(REPO_ROOT / "mcdreforged.plugin.json", "r", encoding="utf8") as file:
            metadata = json.load(file)

        self.assertIn("lang", metadata.get("resources", []))
        self.assertNotIn("config_templates", metadata.get("resources", []))
        self.assertEqual(metadata["entrypoint"], metadata["id"])
        self.assertTrue(metadata["version"])

    def test_pack_plugin_builds_posix_archive_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = pack_plugin.pack_plugin(REPO_ROOT, Path(temp_dir))

            with zipfile.ZipFile(archive_path, "r") as archive:
                names = archive.namelist()

        self.assertEqual(archive_path.name, "MCDR2Restic_v0.5.0.mcdr")
        self.assertIn("mcdr2restic/__init__.py", names)
        self.assertIn("lang/zh_cn.json", names)
        self.assertIn("lang/zh_tw.json", names)
        self.assertIn("mcdr2restic/config_templates/zh_cn.yml", names)
        self.assertIn("mcdr2restic/config_templates/zh_tw.yml", names)
        self.assertIn("mcdreforged.plugin.json", names)
        self.assertIn("requirements.txt", names)
        self.assertIn("README.md", names)
        self.assertIn("README_EN.md", names)
        self.assertIn("LICENSE", names)
        self.assertIn("mcdr2restic/__init__.py", names)
        for resource_name in ("lang",):
            self.assertTrue(any(name.startswith(resource_name + "/") for name in names))
        self.assertTrue(all("\\" not in name for name in names))
        self.assertFalse(any("__pycache__" in name for name in names))

    def test_release_verification_checks_tag_and_archive_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = pack_plugin.pack_plugin(REPO_ROOT, Path(temp_dir))
            metadata = pack_plugin.load_plugin_metadata(REPO_ROOT)

            verify_plugin.verify_git_tag(metadata, "v0.5.0")
            verify_plugin.verify_archive(REPO_ROOT, archive_path, metadata)

            with self.assertRaises(ValueError):
                verify_plugin.verify_git_tag(metadata, "v0.5.1")
