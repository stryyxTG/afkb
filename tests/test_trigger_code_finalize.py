import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tglol.config import Config
from tglol.db import save_worker, record_worker_request, worker_activity_stats, add_account, claim_accounts_for_worker, get_account, init_db
from tglol.handlers import _finalize_requested_account_stages, _give_out_accounts, _watch_requested_account_codes
from tglol.telegram_service import TelegramCodeMessage


class TriggerCodeFinalizeTests(unittest.IsolatedAsyncioTestCase):
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

    def test_one_received_code_marks_only_its_account_as_issued(self):
        first_id = self.add_account("50001")
        second_id = self.add_account("50002")
        claimed = self.claim_processing(first_id, second_id)

        result = _finalize_requested_account_stages(self.config, claimed, {first_id})

        self.assertEqual(result, {"issued": 1, "restored": 1, "skipped": 0})
        self.assertEqual(get_account(self.config, first_id).account_stage, "issued")
        self.assertEqual(get_account(self.config, second_id).account_stage, "nereg")

    def test_timeout_restore_preserves_reg_service_metadata(self):
        account_id = self.add_account("60001", stage="reg", services="imo,bebe")
        claimed = self.claim_processing(account_id)

        result = _finalize_requested_account_stages(self.config, claimed, set())
        account = get_account(self.config, account_id)

        self.assertEqual(result, {"issued": 0, "restored": 1, "skipped": 0})
        self.assertEqual(account.account_stage, "reg")
        self.assertEqual(account.registration_services, "imo,bebe")
        self.assertEqual(account.registration_service, "imo")



    async def test_watcher_moves_only_delivered_account_to_issued(self):
        first_id = self.add_account("70001")
        second_id = self.add_account("70002")
        claimed = self.claim_processing(first_id, second_id)
        first, second = claimed
        delivered = []

        async def fake_poll(config, account, *, started_at, seen_keys=None):
            if account.id == first.id:
                return account, TelegramCodeMessage(code="12345", key="telegram:1:0", date=started_at), False
            return account, None, True

        async def fake_send(*args, **kwargs):
            delivered.append(kwargs["account"].id)

        with patch("tglol.handlers._poll_new_telegram_code_message", side_effect=fake_poll), patch(
            "tglol.handlers._send_code_delivery", side_effect=fake_send
        ):
            await _watch_requested_account_codes(
                SimpleNamespace(),
                self.config,
                claimed,
                chat_id=-100123,
                requester_user_id=1,
                reply_to_message_id=10,
                started_at=datetime.now(timezone.utc),
            )

        self.assertEqual(delivered, [first_id])
        self.assertEqual(get_account(self.config, first_id).account_stage, "issued")
        self.assertEqual(get_account(self.config, second_id).account_stage, "nereg")


    async def test_failed_number_delivery_restores_processing_accounts(self):
        account_id = self.add_account("71001")
        account = get_account(self.config, account_id)
        Path(account.session_path).write_bytes(b"session")
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1), text="тг",
            chat=SimpleNamespace(id=-100123), message_id=10,
            answer=AsyncMock(side_effect=RuntimeError("chat write forbidden")),
        )
        with patch("tglol.handlers._account_connection_params", return_value=(1, "hash", {})):
            await _give_out_accounts(message, SimpleNamespace(), self.config)
        self.assertEqual(get_account(self.config, account_id).account_stage, "nereg")
    async def test_ten_accounts_five_deliveries_rest_wait_until_timeout(self):
        save_worker(self.config, user_id=2, first_name="Worker", last_name=None,
                    username="worker", remaining_limit=0, created_by=1)
        request_id = record_worker_request(self.config, 2, 10)
        account_ids = [self.add_account(str(80000 + index)) for index in range(10)]
        claimed = self.claim_processing(*account_ids)
        delivered = []
        clock = [0.0]
        rounds = [0]

        async def fake_poll(config, account, *, started_at, seen_keys=None):
            ready = account_ids[:2] if rounds[0] == 0 else account_ids[:5]
            if account.id in ready:
                return account, TelegramCodeMessage(
                    code="12345", key=f"telegram:{account.id}", date=started_at
                ), False
            return account, None, False

        async def fake_send(*args, **kwargs):
            delivered.append(kwargs["account"].id)

        async def fake_sleep(seconds):
            for account_id in account_ids:
                expected = "issued" if account_id in delivered else "processing"
                self.assertEqual(get_account(self.config, account_id).account_stage, expected)
            self.assertEqual(self.claim_processing(*account_ids), [])
            rounds[0] += 1
            clock[0] = 10.0 if rounds[0] == 1 else 601.0

        with patch("tglol.handlers._poll_new_telegram_code_message", side_effect=fake_poll), patch(
            "tglol.handlers._send_code_delivery", side_effect=fake_send
        ), patch("tglol.handlers.asyncio.sleep", side_effect=fake_sleep), patch(
            "tglol.handlers.time", SimpleNamespace(monotonic=lambda: clock[0])
        ):
            await _watch_requested_account_codes(
                SimpleNamespace(), self.config, claimed,
                chat_id=-100123, requester_user_id=2, reply_to_message_id=10,
                started_at=datetime.now(timezone.utc), worker_request_id=request_id,
            )

        self.assertEqual(delivered, account_ids[:5])
        self.assertEqual(worker_activity_stats(self.config)[2]["delivered"], 5)
        for account_id in account_ids:
            expected = "issued" if account_id in delivered else "nereg"
            self.assertEqual(get_account(self.config, account_id).account_stage, expected)
        self.assertEqual(
            [account.id for account in self.claim_processing(*account_ids)], account_ids[5:]
        )

if __name__ == "__main__":
    unittest.main()