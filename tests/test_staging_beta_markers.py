# -*- coding: utf-8 -*-
try:
    from .support import REPO_ROOT, json, unittest
except ImportError:
    from support import REPO_ROOT, json, unittest

from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.default_config_templates import get_default_config_template
from mcdr2restic.notifications.message_templates import render_message
from mcdr2restic.staging import (
    staging_backup,
    staging_copy,
    staging_hint,
    staging_maintenance,
    staging_purge,
    staging_repository,
    staging_state,
    staging_warning,
    staging_worker,
)


class BetaMarkerTest(unittest.TestCase):
    def test_config_template_marks_staging_beta(self):
        template = get_default_config_template("zh_cn")
        staging_block = template[template.index("staging:") : template.index("onebot:")]

        self.assertIn("[beta]", staging_block)

    def test_docs_and_readme_entries_mark_beta(self):
        staging_doc = (REPO_ROOT / "docs" / "staging.md").read_text(encoding="utf8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf8")
        readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf8")
        navigation = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf8")

        self.assertIn("beta", staging_doc[:500])
        self.assertIn("中转库（beta", readme)
        self.assertIn("Staging repository (beta", readme_en)
        self.assertIn("中转库（beta）: staging.md", navigation)

    def test_staging_modules_docstrings_mark_beta(self):
        modules = (
            staging_backup,
            staging_copy,
            staging_hint,
            staging_maintenance,
            staging_purge,
            staging_repository,
            staging_state,
            staging_warning,
            staging_worker,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertIn("beta", (module.__doc__ or "").lower())

    def test_staging_log_messages_mark_beta(self):
        for language in ("zh_cn", "en_us"):
            path = REPO_ROOT / "mcdr2restic" / "lang" / "{}.json".format(language)
            messages = json.loads(path.read_text(encoding="utf8"))
            for key, value in messages.items():
                if not (
                    key.startswith("error.staging")
                    or key.startswith("warn.staging")
                    or key.startswith("info.staging")
                ):
                    continue
                with self.subTest(language=language, key=key):
                    self.assertIn("[beta]", value)

    def test_default_hint_contains_beta_default_state_costs_docs_and_enable(self):
        cfg = build_default_config()
        template = cfg["messages"]["staging_hint"]
        rendered = render_message(
            "staging_hint",
            {"documentation_url": "https://m2r.pfdr.cc/staging/"},
            cfg,
        )

        for fragment in ("[beta]", "默认关闭", "save-off", "更复杂", "staging.enabled"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, template)
        self.assertIn("https://m2r.pfdr.cc/staging/", rendered)


if __name__ == "__main__":
    unittest.main()
