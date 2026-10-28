"""Regression tests for the lint gate and genuine issues found by static analysis."""

# Tests intentionally call internal setup helpers and isolate optional imports.
# pylint: disable=protected-access,import-outside-toplevel

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ci_tools import lint


class TestSchemaLint(TestCase):
    """Schema awareness must not turn off detection of misspelled fields."""

    def test_declared_fields_pass_but_typos_fail(self):
        """A declared field is allowed, while an undeclared spelling fails."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "controller.py"
            path.write_text(
                "class Document:\n    pass\n\nclass Controller(Document):\n"
                "    def read(self):\n        return self.real_field, self.misspelled_field\n",
                encoding="utf-8",
            )
            path.with_suffix(".json").write_text(json.dumps({
                "doctype": "DocType", "fields": [{"fieldname": "real_field"}],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pylint", "--errors-only", "--load-plugins=ci_tools.pylint_frappe", str(path)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("misspelled_field", result.stdout)
            self.assertNotIn("'real_field'", result.stdout)


class TestLintRatchet(TestCase):
    """Existing debt cannot exempt errors or allow additional style problems."""

    def test_new_style_debt_fails(self):
        """A budget cannot silently grow."""
        rows = [{"path": "example.py", "symbol": "unused-import", "type": "warning"}]
        with TemporaryDirectory() as directory, patch.object(lint, "BASELINE", Path(directory) / "baseline.json"):
            lint.BASELINE.write_text("{}", encoding="utf-8")
            self.assertFalse(lint.check(rows))
            lint.BASELINE.write_text(json.dumps({"example.py::unused-import": 1}), encoding="utf-8")
            self.assertTrue(lint.check(rows))

    def test_errors_cannot_be_baselined(self):
        """Python errors are never grandfathered."""
        rows = [{"path": "example.py", "symbol": "undefined-variable", "type": "error",
                 "line": 1, "message-id": "E0602", "message": "Undefined variable"}]
        with self.assertRaisesRegex(RuntimeError, "Fix Python errors"):
            lint.check(rows, update=True)


class TestFreshDeskSetup(TestCase):
    """A fresh standard document with an explicit name must be inserted."""

    def test_new_record_uses_insert(self):
        """Fresh named records must not go through update/save."""
        from whatsapp_evolution import setup
        with patch.object(setup, "frappe") as fake:
            fake.db.exists.return_value = False
            setup._save_standard_doc("Sidebar", "Test", {"title": "Test"})
            fake.get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True)
            fake.get_doc.return_value.save.assert_not_called()

    def test_existing_record_uses_save(self):
        """Persistent records keep their normal validation path."""
        from whatsapp_evolution import setup
        with patch.object(setup, "frappe") as fake:
            fake.db.exists.return_value = True
            setup._save_standard_doc("Sidebar", "Test", {"title": "Test"})
            fake.get_doc.return_value.save.assert_called_once_with(ignore_permissions=True)
            fake.get_doc.return_value.insert.assert_not_called()

    def test_v16_sidebar_uses_standard_file_import(self):
        """Standard sidebars need the framework import lifecycle on v16."""
        from whatsapp_evolution import setup
        with patch.object(setup, "frappe") as fake, patch.object(setup, "import_file_by_path") as importer:
            fake.db.table_exists.return_value = True
            fake.get_app_path.return_value = "/app/sidebar.json"
            setup._ensure_workspace_sidebar()
            importer.assert_called_once_with("/app/sidebar.json", force=True)
            fake.get_doc.assert_not_called()
        path = Path(__file__).resolve().parents[1] / "whatsapp_evolution/whatsapp_evolution/sidebar/whatsapp_evolution.json"
        sidebar = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(any(item.get("link_to") == "WhatsApp Deletion Request" for item in sidebar["items"]))


class TestAttachmentPaths(TestCase):
    """Unknown URLs must raise a useful error, never return an unbound variable."""

    def setUp(self):
        # Path validation does not use native media sniffing. Keep these unit
        # tests portable on machines without the optional libmagic library.
        self.enterContext(patch.dict(sys.modules, {"magic": MagicMock()}))

    def test_local_files_use_framework_resolution(self):
        """Delegate private/public paths to Frappe's resolver."""
        from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_templates import whatsapp_templates as templates
        with patch("frappe.utils.file_manager.get_file_path", return_value="/safe/file.pdf") as resolver:
            actual = templates.WhatsAppTemplates.get_absolute_path(None, "/private/files/file.pdf")
            self.assertEqual(actual, "/safe/file.pdf")
            resolver.assert_called_once_with("/private/files/file.pdf")

    def test_remote_or_empty_paths_are_rejected(self):
        """Reject paths that cannot identify a local attachment."""
        from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_templates import whatsapp_templates
        with patch.object(whatsapp_templates.frappe, "throw", side_effect=ValueError), \
             patch.object(whatsapp_templates, "_", side_effect=lambda value: value):
            for path in (None, "", "https://untrusted.example/file.pdf", "../file.pdf"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    whatsapp_templates.WhatsAppTemplates.get_absolute_path(None, path)
