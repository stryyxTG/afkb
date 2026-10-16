import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tglol.handlers import (
    _contains_trigger_word, _parse_trigger_request_count, _parse_telegram_chat_id,
    _set_config_trigger_chat, _set_config_trigger_word, _set_config_trigger_max_accounts,
    _set_config_code_watch_minutes, _set_config_workers_enabled, save_trigger_chat,
)
from tglol.keyboards import accounts_menu, trigger_menu
import test_workers


class TriggerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.case = test_workers.AccountClaimTests()
        self.case.setUp()
        self.config = self.case.config

    def tearDown(self):
        self.case.tearDown()

    def test_parser_accepts_supergroup_id(self):
        self.assertEqual(_parse_telegram_chat_id("-1001234567890"), -1001234567890)
        self.assertIsNone(_parse_telegram_chat_id("chat"))
        self.assertIsNone(_parse_telegram_chat_id("0"))

    def test_trigger_chat_is_persisted_and_applied(self):
        with patch("tglol.handlers._write_env_value") as write_env:
            _set_config_trigger_chat(self.config, -1001234567890)
            self.assertEqual(self.config.trigger_chat_id, -1001234567890)
            write_env.assert_called_once_with("TRIGGER_CHAT_ID", "-1001234567890")

    def test_custom_word_matches_and_parses_count(self):
        self.assertTrue(_contains_trigger_word("give 5", "give"))
        self.assertFalse(_contains_trigger_word("forgive 5", "give"))
        self.assertEqual(_parse_trigger_request_count("give 7", "give"), 7)
        self.assertEqual(_parse_trigger_request_count("give 99", "give", 7), 7)

    def test_trigger_word_is_persisted_and_applied(self):
        with patch("tglol.handlers._write_env_value") as write_env:
            _set_config_trigger_word(self.config, "give")
        self.assertEqual(self.config.trigger_word, "give")
        write_env.assert_called_once_with("TRIGGER_WORD", "give")

    def test_delivery_settings_are_persisted_and_applied(self):
        with patch("tglol.handlers._write_env_value") as write_env:
            _set_config_trigger_max_accounts(self.config, 7)
            _set_config_code_watch_minutes(self.config, 12)
            _set_config_workers_enabled(self.config, False)
        self.assertEqual(self.config.trigger_max_accounts, 7)
        self.assertEqual(self.config.code_watch_minutes, 12)
        self.assertFalse(self.config.workers_enabled)
        self.assertEqual(write_env.call_args_list[0].args, ("TRIGGER_MAX_ACCOUNTS", "7"))
        self.assertEqual(write_env.call_args_list[1].args, ("CODE_WATCH_MINUTES", "12"))
        self.assertEqual(write_env.call_args_list[2].args, ("WORKERS_ENABLED", "0"))

    def test_trigger_button_and_disable_action_are_present(self):
        main_callbacks = [button.callback_data for row in accounts_menu().inline_keyboard for button in row]
        self.assertIn("trigger:menu", main_callbacks)
        callbacks = [button.callback_data for row in trigger_menu(has_trigger=True).inline_keyboard for button in row]
        self.assertIn("trigger:clear", callbacks)
        self.assertIn("trigger:word", callbacks)
        self.assertIn("trigger:max", callbacks)
        self.assertIn("trigger:minutes", callbacks)
        self.assertIn("trigger:workers_toggle", callbacks)


class TriggerSettingsMessageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = test_workers.AccountClaimTests()
        self.case.setUp()
        self.config = self.case.config

    def tearDown(self):
        self.case.tearDown()

    async def test_chat_save_reports_env_permission_error(self):
        message = SimpleNamespace(text="-1001234567890", answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        with patch("tglol.handlers._set_config_trigger_chat", side_effect=PermissionError):
            await save_trigger_chat(message, state, self.config)
        state.clear.assert_not_awaited()
        self.assertIn(".env", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
