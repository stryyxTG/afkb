import unittest
from pathlib import Path

from tglol.config import Config
from tglol.desktop_profile import APP_VERSIONS, WINDOWS_DEVICES, WINDOWS_SDKS, generated_account_json, random_desktop_runtime


class DesktopProfileTests(unittest.TestCase):
    def test_random_desktop_runtime_uses_only_windows_profile(self):
        for _ in range(100):
            runtime = random_desktop_runtime()
            self.assertIn(runtime["device"], WINDOWS_DEVICES)
            self.assertIn(runtime["sdk"], WINDOWS_SDKS)
            self.assertEqual(runtime["app_version"], "7.0.6 x64")
            self.assertNotIn("Linux", runtime["device"])
            self.assertNotIn("Linux", runtime["sdk"])

    def test_app_versions_has_latest_desktop_version_only(self):
        self.assertEqual(APP_VERSIONS, ["7.0.6 x64"])

    def test_generated_json_preserves_runtime_proxy(self):
        config = Config(
            bot_token="token",
            admin_ids=frozenset({1}),
            telegram_api_id=2040,
            telegram_api_hash="hash",
            telegram_proxy="host:1234:user:pass",
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
        data = generated_account_json(
            config,
            runtime={
                "device": "Desktop",
                "sdk": "Windows 11 x64",
                "app_version": "7.0.6 x64",
                "proxy": "host:1234:user:pass",
            },
        )
        self.assertEqual(data["proxy"], "host:1234:user:pass")


if __name__ == "__main__":
    unittest.main()