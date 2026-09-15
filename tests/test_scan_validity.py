import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tglol.config import Config
from tglol.db import add_account, get_account, init_db
from tglol.handlers import check_account_validity


class ScanValidityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.config = Config(
            bot_token="token",
            admin_ids=frozenset({1}),
            telegram_api_id=123,
            telegram_api_hash="hash",
            telegram_proxy=None,
            bot_parse_mode="HTML",
            data_dir=self.tmpdir,
            sessions_dir=self.tmpdir / "sessions",
            json_dir=self.tmpdir / "json",
            temp_dir=self.tmpdir / "tmp",
            db_path=self.tmpdir / "bot.sqlite3",
            default_lang_code="en",
            default_system_lang_code="en-US",
            default_lang_pack="tdesktop",
            trigger_chat_id=None,
        )
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.config.json_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.config)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def add_account(self) -> int:
        session_path = self.config.sessions_dir / "account.session"
        json_path = self.config.json_dir / "account.json"
        session_path.write_bytes(b"session")
        json_path.write_text('{"phone":"+15550001111","user_id":12345}', encoding="utf-8")
        return add_account(
            self.config,
            {
                "phone": "+15550001111",
                "telegram_user_id": 12345,
                "username": "user",
                "first_name": "User",
                "last_name": None,
                "session_path": str(session_path),
                "json_original_path": str(json_path),
                "json_effective_path": str(json_path),
                "json_source": "test",
                "twofa_password": None,
                "source_type": "test",
                "status": "active",
                "created_by": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )

    async def check_with_result(self, low_level_result: dict):
        account_id = self.add_account()
        account = get_account(self.config, account_id)
        with patch("tglol.handlers.inspect_session_with_freeze_check", return_value=low_level_result):
            result = await check_account_validity(account, self.config)
        return account_id, result

    async def test_frozen_account_keeps_frozen_status_and_is_saved(self):
        account_id, result = await self.check_with_result(
            {"ok": False, "status": "frozen", "account_status": "frozen", "reason": "user_deactivated_ban"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(result["reason"], "user_deactivated_ban")
        self.assertEqual(get_account(self.config, account_id).status, "frozen")

    async def test_flood_wait_is_skipped_and_status_is_saved(self):
        account_id, result = await self.check_with_result(
            {"ok": False, "status": "skipped", "account_status": "skipped", "reason": "flood_wait_60"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "flood_wait_60")
        self.assertEqual(get_account(self.config, account_id).status, "skipped")

    async def test_need_2fa_keeps_status_and_is_not_ok(self):
        account_id, result = await self.check_with_result(
            {"ok": False, "status": "need_2fa", "account_status": "need_2fa", "reason": "session_password_needed"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "need_2fa")
        self.assertEqual(get_account(self.config, account_id).status, "need_2fa")


if __name__ == "__main__":
    unittest.main()