"""Keep desktop message catalogs complete when backend copy changes."""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UiLocalizationTests(unittest.TestCase):
    def test_backend_message_catalog_is_current(self):
        script = ROOT / "desktop/scripts/extract-backend-messages.py"
        spec = importlib.util.spec_from_file_location("backend_ui_messages", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.DESTINATION.read_text(encoding="utf-8"), module.serialized())

    def test_all_application_messages_have_translations_and_matching_parameters(self):
        src = ROOT / "desktop/src"
        phrases = set(json.loads((src / "ui-source.json").read_text(encoding="utf-8")))
        phrases.update(json.loads((src / "backend-source.json").read_text(encoding="utf-8")))
        for filename in ("messagesEn.json", "messagesHk.json"):
            dictionary = json.loads((src / filename).read_text(encoding="utf-8"))
            self.assertFalse(phrases - dictionary.keys(), filename)
            for phrase in phrases:
                with self.subTest(language=filename, phrase=phrase):
                    self.assertTrue(dictionary[phrase].strip())
                    self.assertEqual(sorted(re.findall(r"\{\w+\}", phrase)),
                                     sorted(re.findall(r"\{\w+\}", dictionary[phrase])))
