import unittest
from unittest.mock import patch

from tglol.handlers import _parse_telegram_chat_id, _set_config_trigger_chat
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

    def test_trigger_button_and_disable_action_are_present(self):
        main_callbacks = [button.callback_data for row in accounts_menu().inline_keyboard for button in row]
        self.assertIn("trigger:menu", main_callbacks)
        callbacks = [button.callback_data for row in trigger_menu(has_trigger=True).inline_keyboard for button in row]
        self.assertIn("trigger:clear", callbacks)


if __name__ == "__main__":
    unittest.main()
