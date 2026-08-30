import shutil
import tempfile
import unittest
from pathlib import Path

from tglol.config import Config
from tglol.db import add_account, claim_accounts_for_worker, delete_code_receiver, get_account, get_code_receiver, get_issue_stats, increment_issue_stats, init_db, list_accounts_by_scope, reset_issue_stats, save_code_receiver


class AccountClaimTests(unittest.TestCase):
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

    def add_account(self, phone: str) -> int:
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
                "status": "active",
                "created_by": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )

    def test_claim_can_reserve_accounts_as_processing_without_workers(self):
        first_id = self.add_account("40001")
        second_id = self.add_account("40002")

        claimed = claim_accounts_for_worker(
            self.config,
            [first_id],
            worker_id=None,
            reserve_stage="processing",
        )

        self.assertEqual([account.id for account in claimed], [first_id])
        self.assertEqual(get_account(self.config, first_id).account_stage, "processing")
        self.assertEqual(claim_accounts_for_worker(self.config, [first_id], worker_id=None), [])
        visible = list_accounts_by_scope(self.config, excluded_account_stage=("issued", "processing"))
        self.assertEqual([account.id for account in visible], [second_id])


    def test_code_receiver_is_singleton_and_separate_from_accounts(self):
        now = "2026-01-01T00:00:00+00:00"
        values = {
            "phone": "+10000000000",
            "telegram_user_id": 777,
            "username": "receiver",
            "first_name": "Code",
            "last_name": "Receiver",
            "session_path": str(self.tmpdir / "receiver.session"),
            "json_original_path": str(self.tmpdir / "receiver.json"),
            "json_effective_path": str(self.tmpdir / "receiver.json"),
            "json_source": "uploaded",
            "twofa_password": "hidden",
            "source_type": "zip",
            "status": "active",
            "created_by": 1,
            "created_at": now,
            "updated_at": now,
        }

        save_code_receiver(self.config, values)
        receiver = get_code_receiver(self.config)

        self.assertIsNotNone(receiver)
        self.assertEqual(receiver.phone, "+10000000000")
        self.assertEqual(list_accounts_by_scope(self.config), [])
        self.assertIsNotNone(delete_code_receiver(self.config))
        self.assertIsNone(get_code_receiver(self.config))

    def test_issue_stats_increment_and_reset(self):
        self.assertEqual(get_issue_stats(self.config).issued_since_reset, 0)

        increment_issue_stats(self.config, 1)
        increment_issue_stats(self.config, 2)

        self.assertEqual(get_issue_stats(self.config).issued_since_reset, 3)
        previous = reset_issue_stats(self.config)
        self.assertEqual(previous.issued_since_reset, 3)
        self.assertEqual(get_issue_stats(self.config).issued_since_reset, 0)

if __name__ == "__main__":
    unittest.main()
