"""Provider-aware LLM logging, with historical version references preserved."""

import sqlite3
import unittest
from unittest.mock import patch

from api import model_registry


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(self.db.close)
        self.env = patch.dict("os.environ", {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def current_local_version(self, settings):
        with patch.dict("os.environ", settings):
            version_id = model_registry.sync(self.db.cursor())["llm_local"]
        label, fingerprint = self.db.execute(
            "SELECT version_label, fingerprint FROM model_version WHERE id = ?",
            (version_id,),
        ).fetchone()
        return version_id, label, fingerprint

    def test_openai_records_served_model_instead_of_stale_ollama_setting(self):
        _, label, fingerprint = self.current_local_version({
            "LLM_PROVIDER": "openai", "OPENAI_MODEL": "sideproject-llm",
            "OLLAMA_MODEL": "obsolete-model",
        })
        self.assertEqual(label, "sideproject-llm")
        self.assertEqual(fingerprint, "env:OPENAI_MODEL=sideproject-llm")

    def test_localai_uses_openai_model(self):
        _, label, _ = self.current_local_version({
            "LLM_PROVIDER": "localai", "OPENAI_MODEL": "served-model",
            "OLLAMA_MODEL": "obsolete-model",
        })
        self.assertEqual(label, "served-model")

    def test_ollama_uses_ollama_model(self):
        _, label, fingerprint = self.current_local_version({
            "LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "qwen3:4b-instruct",
            "OPENAI_MODEL": "other-model",
        })
        self.assertEqual(label, "qwen3:4b-instruct")
        self.assertEqual(fingerprint, "env:OLLAMA_MODEL=qwen3:4b-instruct")

    def test_unconfigured_model_is_unset_instead_of_an_invented_default(self):
        self.assertEqual(self.current_local_version({})[1], "unset")
        self.assertEqual(self.current_local_version({
            "LLM_PROVIDER": "openai", "OLLAMA_MODEL": "unrelated-model",
        })[1], "unset")

    def test_provider_and_model_whitespace_are_normalized(self):
        _, label, _ = self.current_local_version({
            "LLM_PROVIDER": " OpenAI ", "OPENAI_MODEL": " sideproject-llm ",
        })
        self.assertEqual(label, "sideproject-llm")

    def test_switch_preserves_old_chat_attribution_and_reuses_versions(self):
        old_settings = {"LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "old-model"}
        old_id, _, _ = self.current_local_version(old_settings)
        self.db.execute(
            "CREATE TABLE chat_messages (model_version_id INTEGER REFERENCES model_version(id))"
        )
        self.db.execute("INSERT INTO chat_messages VALUES (?)", (old_id,))
        new_settings = {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "sideproject-llm"}
        new_id, _, _ = self.current_local_version(new_settings)
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(self.current_local_version(new_settings)[0], new_id)
        self.assertEqual(self.current_local_version(old_settings)[0], old_id)
        self.assertEqual(self.db.execute(
            "SELECT mv.version_label FROM chat_messages cm "
            "JOIN model_version mv ON mv.id = cm.model_version_id"
        ).fetchone()[0], "old-model")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM model_version mv JOIN model_lookup ml "
            "ON ml.id = mv.model_id WHERE ml.name = 'llm_local'"
        ).fetchone()[0], 2)

    def test_claude_version_still_uses_its_own_configuration(self):
        with patch.dict("os.environ", {
            "LLM_PROVIDER": "openai", "OPENAI_MODEL": "sideproject-llm",
            "CLAUDE_MODEL": "configured-claude-model",
        }):
            version_id = model_registry.sync(self.db.cursor())["llm_claude"]
        self.assertEqual(self.db.execute(
            "SELECT version_label FROM model_version WHERE id = ?", (version_id,)
        ).fetchone()[0], "configured-claude-model")


if __name__ == "__main__":
    unittest.main()
