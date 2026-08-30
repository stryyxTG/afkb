import shutil
import tempfile
import unittest
from pathlib import Path

from tglol.config import Config
from tglol.db import add_account, get_account, init_db, list_accounts_by_scope


class ScopeFilteringTests(unittest.TestCase):
    def test_list_accounts_by_scope_accepts_excluded_account_stage(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            base = Path(tmpdir)
            base.mkdir(parents=True, exist_ok=True)
            config = Config(
                bot_token="token",
                admin_ids=frozenset({1}),
                telegram_api_id=1,
                telegram_api_hash="hash",
            telegram_proxy=None,
                bot_parse_mode="HTML",
                data_dir=base / "storage",
                sessions_dir=base / "sessions",
                json_dir=base / "json",
                temp_dir=base / "tmp",
                db_path=base / "bot.sqlite3",
                default_lang_code="en",
                default_system_lang_code="en-US",
                default_lang_pack="tdesktop",
                trigger_chat_id=None,
            )
            init_db(config)

            clean_account_id = add_account(
                config,
                {
                    "phone": "+70000000000",
                    "telegram_user_id": 1,
                    "username": "clean",
                    "first_name": "Clean",
                    "last_name": "User",
                    "session_path": str(base / "clean.session"),
                    "json_original_path": None,
                    "json_effective_path": None,
                    "json_source": "manual",
                    "twofa_password": None,
                    "source_type": "manual",
                    "account_stage": "nereg",
                    "registration_service": None,
                    "registration_services": None,
                    "status": "ok",
                    "created_by": None,
                    "created_at": "now",
                    "updated_at": "now",
                },
            )
            add_account(
                config,
                {
                    "phone": "+70000000001",
                    "telegram_user_id": 2,
                    "username": "issued",
                    "first_name": "Issued",
                    "last_name": "User",
                    "session_path": str(base / "issued.session"),
                    "json_original_path": None,
                    "json_effective_path": None,
                    "json_source": "manual",
                    "twofa_password": None,
                    "source_type": "manual",
                    "account_stage": "issued",
                    "registration_service": None,
                    "registration_services": None,
                    "status": "ok",
                    "created_by": None,
                    "created_at": "now",
                    "updated_at": "now",
                },
            )

            accounts = list_accounts_by_scope(config, excluded_account_stage="issued")

            self.assertEqual([account.id for account in accounts], [clean_account_id])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_init_db_recovers_stuck_processing_accounts_as_issued(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            base = Path(tmpdir)
            config = Config(
                bot_token="token",
                admin_ids=frozenset({1}),
                telegram_api_id=1,
                telegram_api_hash="hash",
                telegram_proxy=None,
                bot_parse_mode="HTML",
                data_dir=base / "storage",
                sessions_dir=base / "sessions",
                json_dir=base / "json",
                temp_dir=base / "tmp",
                db_path=base / "bot.sqlite3",
                default_lang_code="en",
                default_system_lang_code="en-US",
                default_lang_pack="tdesktop",
                trigger_chat_id=None,
            )
            init_db(config)
            account_id = add_account(
                config,
                {
                    "phone": "+70000000002",
                    "telegram_user_id": 3,
                    "username": "processing",
                    "first_name": "Processing",
                    "last_name": "User",
                    "session_path": str(base / "processing.session"),
                    "json_original_path": None,
                    "json_effective_path": None,
                    "json_source": "manual",
                    "twofa_password": None,
                    "source_type": "manual",
                    "account_stage": "processing",
                    "registration_service": None,
                    "registration_services": None,
                    "status": "ok",
                    "created_by": None,
                    "created_at": "now",
                    "updated_at": "now",
                },
            )

            init_db(config)

            self.assertEqual(get_account(config, account_id).account_stage, "issued")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
