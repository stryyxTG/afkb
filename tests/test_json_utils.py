import unittest
from pathlib import Path

from tglol.config import Config
from tglol.json_utils import pick_api, runtime_from_json


class JsonUtilsTests(unittest.TestCase):
    def config(self):
        return Config(
            bot_token="token",
            admin_ids=frozenset({1}),
            telegram_api_id=2040,
            telegram_api_hash="config_hash",
            telegram_proxy=None,
            bot_parse_mode="HTML",
            data_dir=Path("storage"),
            sessions_dir=Path("storage/sessions"),
            json_dir=Path("storage/json"),
            temp_dir=Path("storage/tmp"),
            db_path=Path("storage/bot.sqlite3"),
            default_lang_code="en",
            default_system_lang_code="en-US",
            default_lang_pack="tdesktop",
            trigger_chat_id=None,
        )

    def test_pick_api_prefers_app_credentials(self):
        self.assertEqual(
            pick_api(
                {
                    "app_id": 4,
                    "app_hash": "android_hash",
                    "api_id": 2040,
                    "api_hash": "desktop_hash",
                },
                self.config(),
            ),
            (4, "android_hash"),
        )

    def test_pick_api_falls_back_to_api_credentials_then_config(self):
        self.assertEqual(pick_api({"api_id": 8, "api_hash": "api_hash"}, self.config()), (8, "api_hash"))
        self.assertEqual(pick_api({"app_id": "", "app_hash": ""}, self.config()), (2040, "config_hash"))

    def test_runtime_uses_json_device_but_forces_safe_language(self):
        runtime = runtime_from_json(
            {
                "device_model": "Pixel 8",
                "device": "Other",
                "system_version": "Android 15",
                "sdk": "SDK 31",
                "app_version": "12.8.3 (69799)",
                "lang_code": "ru",
                "system_lang_code": "ru-ru",
                "lang_pack": "android",
            }
        )
        self.assertEqual(runtime["device"], "Pixel 8")
        self.assertEqual(runtime["sdk"], "Android 15")
        self.assertEqual(runtime["app_version"], "12.8.3 (69799)")
        self.assertEqual(runtime["lang_code"], "en")
        self.assertEqual(runtime["system_lang_code"], "en-US")
        self.assertEqual(runtime["lang_pack"], "android")

    def test_runtime_defaults_match_import_client_defaults(self):
        runtime = runtime_from_json({})
        self.assertEqual(runtime["device"], "Desktop")
        self.assertEqual(runtime["sdk"], "Windows 11")
        self.assertEqual(runtime["app_version"], "6.9.3 x64")


if __name__ == "__main__":
    unittest.main()