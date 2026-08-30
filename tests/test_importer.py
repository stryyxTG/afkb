import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tglol.config import Config
from tglol.db import init_db
from tglol.importer import SessionValidationError, import_session_account, import_zip


class ImporterJsonRequirementTests(unittest.IsolatedAsyncioTestCase):
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
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.config)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def account_count(self) -> int:
        connection = sqlite3.connect(self.config.db_path)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
        finally:
            connection.close()

    async def test_import_session_requires_json_path(self):
        with self.assertRaisesRegex(ValueError, "matching JSON"):
            await import_session_account(
                self.config,
                session_path=self.tmpdir / "account.session",
                source_type="test",
                created_by=1,
                json_path=None,
            )

    async def test_zip_session_without_json_is_rejected_before_copy(self):
        zip_path = self.tmpdir / "accounts.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("account.session", b"session")

        results, summary = await import_zip(self.config, zip_path=zip_path, created_by=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "error")
        self.assertIn("matching JSON", results[0].note)
        self.assertIn("Без JSON: 1", summary)
        self.assertEqual(list(self.config.sessions_dir.glob("*.session")), [])

    async def test_import_session_rejects_when_inspect_fails(self):
        session_path = self.tmpdir / "account.session"
        json_path = self.tmpdir / "account.json"
        session_path.write_bytes(b"dummy")
        json_path.write_text(
            '{"phone":"+15550001111","user_id":12345,"username":"user","proxy":[2,"host",1080,true,"u","p"]}',
            encoding="utf-8",
        )

        with patch("tglol.importer.inspect_session", side_effect=RuntimeError("proxy died")):
            with self.assertRaisesRegex(SessionValidationError, "proxy died"):
                await import_session_account(
                    self.config,
                    session_path=session_path,
                    source_type="test",
                    created_by=1,
                    json_path=json_path,
                )

        self.assertEqual(self.account_count(), 0)

    async def test_import_session_rejects_inactive_status(self):
        session_path = self.tmpdir / "account.session"
        json_path = self.tmpdir / "account.json"
        session_path.write_bytes(b"dummy")
        json_path.write_text('{"phone":"+15550001111","user_id":12345}', encoding="utf-8")

        with patch("tglol.importer.inspect_session", return_value=("unauthorized", None, None)):
            with self.assertRaisesRegex(SessionValidationError, "Session is not active: unauthorized"):
                await import_session_account(
                    self.config,
                    session_path=session_path,
                    source_type="test",
                    created_by=1,
                    json_path=json_path,
                )

        self.assertEqual(self.account_count(), 0)

    async def test_zip_import_counts_session_with_json_as_error_when_inspect_fails(self):
        zip_path = self.tmpdir / "accounts.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("account.session", b"session")
            archive.writestr("account.json", '{"phone":"+15550001111","user_id":12345}')

        with patch("tglol.importer.inspect_session", side_effect=RuntimeError("connect failed")):
            results, summary = await import_zip(self.config, zip_path=zip_path, created_by=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].account_id, 0)
        self.assertEqual(results[0].status, "error")
        self.assertIn("connect failed", results[0].note)
        self.assertIn("Импортировано: 0", summary)
        self.assertIn("Ошибок: 1", summary)
        self.assertEqual(list(self.config.sessions_dir.glob("*.session")), [])
        self.assertEqual(list(self.config.json_dir.glob("*.json")), [])



    async def test_zip_import_retries_transient_inspect_error(self):
        zip_path = self.tmpdir / "accounts.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("account.session", b"session")
            archive.writestr("account.json", '{"phone":"+15550001111","user_id":12345}')

        with patch(
            "tglol.importer.inspect_session",
            side_effect=[RuntimeError("connect failed"), ("active", None, None)],
        ) as inspect_mock:
            results, summary = await import_zip(self.config, zip_path=zip_path, created_by=1)

        self.assertEqual(inspect_mock.call_count, 2)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].account_id, 0)
        self.assertEqual(results[0].status, "active")
        self.assertEqual(results[0].phone, "+15550001111")
        self.assertIn("Импортировано: 1", summary)
        self.assertIn("Ошибок: 0", summary)
        self.assertIn("Повторных попыток: 1", summary)
        self.assertEqual(len(list(self.config.sessions_dir.glob("*.session"))), 1)
        self.assertEqual(len(list(self.config.json_dir.glob("*.json"))), 1)

    async def test_zip_import_does_not_retry_inactive_session(self):
        zip_path = self.tmpdir / "accounts.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("account.session", b"session")
            archive.writestr("account.json", '{"phone":"+15550001111","user_id":12345}')

        with patch("tglol.importer.inspect_session", return_value=("unauthorized", None, None)) as inspect_mock:
            results, summary = await import_zip(self.config, zip_path=zip_path, created_by=1)

        self.assertEqual(inspect_mock.call_count, 1)
        self.assertEqual(results[0].account_id, 0)
        self.assertEqual(results[0].status, "error")
        self.assertIn("Session is not active", results[0].note)
        self.assertNotIn("Повторных попыток", summary)
        self.assertEqual(list(self.config.sessions_dir.glob("*.session")), [])
        self.assertEqual(list(self.config.json_dir.glob("*.json")), [])

if __name__ == "__main__":
    unittest.main()
