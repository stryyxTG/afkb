import shutil
import tempfile
import unittest
from pathlib import Path

from tglol.config import Config
from tglol.db import add_account, claim_accounts_for_worker, get_account, init_db
from tglol.handlers import _finalize_requested_account_stages


class TriggerCodeFinalizeTests(unittest.TestCase):
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
            trigger_chat_id=-100123,
        )
        init_db(self.config)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def add_account(self, phone: str, *, stage: str = "nereg", services: str | None = None) -> int:
        return add_account(
            self.config,
            {
                "phone": phone,
                "telegram_user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None,
                "session_path": str(self.tmpdir / f"{phone}.session"),
                "json_original_path": None,
                "json_effective_path": None,
                "json_source": "test",
                "twofa_password": None,
                "source_type": "test",
                "account_stage": stage,
                "registration_service": services.split(",", 1)[0] if services else None,
                "registration_services": services,
                "status": "active",
                "created_by": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )

    def claim_processing(self, *account_ids: int):
        return claim_accounts_for_worker(
            self.config,
            list(account_ids),
            worker_id=None,
            reserve_stage="processing",
        )

    def test_no_received_codes_returns_processing_accounts_to_clean(self):
        first_id = self.add_account("40001")
        second_id = self.add_account("40002")
        claimed = self.claim_processing(first_id, second_id)

        result = _finalize_requested_account_stages(self.config, claimed, set())

        self.assertEqual(result, {"issued": 0, "restored": 2, "skipped": 0})
        self.assertEqual(get_account(self.config, first_id).account_stage, "nereg")
        self.assertEqual(get_account(self.config, second_id).account_stage, "nereg")

    def test_one_received_code_marks_entire_request_as_issued(self):
        first_id = self.add_account("50001")
        second_id = self.add_account("50002")
        claimed = self.claim_processing(first_id, second_id)

        result = _finalize_requested_account_stages(self.config, claimed, {first_id})

        self.assertEqual(result, {"issued": 2, "restored": 0, "skipped": 0})
        self.assertEqual(get_account(self.config, first_id).account_stage, "issued")
        self.assertEqual(get_account(self.config, second_id).account_stage, "issued")

    def test_timeout_restore_preserves_reg_service_metadata(self):
        account_id = self.add_account("60001", stage="reg", services="imo,bebe")
        claimed = self.claim_processing(account_id)

        result = _finalize_requested_account_stages(self.config, claimed, set())
        account = get_account(self.config, account_id)

        self.assertEqual(result, {"issued": 0, "restored": 1, "skipped": 0})
        self.assertEqual(account.account_stage, "reg")
        self.assertEqual(account.registration_services, "imo,bebe")
        self.assertEqual(account.registration_service, "imo")


if __name__ == "__main__":
    unittest.main()