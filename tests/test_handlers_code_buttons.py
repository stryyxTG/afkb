import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tglol.handlers import (
    _code_message_is_new, _poll_new_telegram_code_message, _send_account_code, _send_code_delivery,
    _terminal_code_poll_status,
)
from tglol.telegram_service import TelegramCodeMessage


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))



class ReplyFailBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if kwargs.get("reply_to_message_id") is not None:
            raise RuntimeError("reply target not found")

class CodeButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_button_is_short_and_keeps_original_reply(self):
        bot = FakeBot()
        account = SimpleNamespace(
            id=1234567890,
            phone="+12536173293",
            twofa_password=None,
            json_original_path=None,
            json_effective_path=None,
        )

        await _send_code_delivery(
            bot,
            chat_id=-1001234567890123,
            account=account,
            code="89621",
            requester_user_id=1234567890123,
            reply_to_message_id=987654321,
        )

        self.assertEqual(len(bot.calls), 1)
        _args, kwargs = bot.calls[0]
        self.assertEqual(kwargs["reply_to_message_id"], 987654321)
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertTrue(button.callback_data.startswith("arc:"))
        self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64)
        self.assertTrue(button.callback_data.endswith(":987654321"))





    async def test_code_delivery_never_falls_back_outside_original_reply(self):
        bot = ReplyFailBot()
        account = SimpleNamespace(
            id=1234567890,
            phone="+12536173293",
            twofa_password=None,
            json_original_path=None,
            json_effective_path=None,
        )

        with self.assertRaisesRegex(RuntimeError, "original request"):
            await _send_code_delivery(
                bot,
                chat_id=-1001234567890123,
                account=account,
                code="89621",
                requester_user_id=1234567890123,
                reply_to_message_id=987654321,
            )

        self.assertEqual(len(bot.calls), 3)
        self.assertTrue(all(call[1]["reply_to_message_id"] == 987654321 for call in bot.calls))

    async def test_code_delivery_includes_twofa_from_json(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "account.json"
            json_path.write_text('{"twoFA":"pass-123"}', encoding="utf-8")
            account = SimpleNamespace(
                id=1234567890,
                phone="+12536173293",
                twofa_password=None,
                json_original_path=str(json_path),
                json_effective_path=str(json_path),
            )

            await _send_code_delivery(
                bot,
                chat_id=-1001234567890123,
                account=account,
                code="89621",
                requester_user_id=1234567890123,
                reply_to_message_id=987654321,
            )

        self.assertEqual(bot.calls[0][1]["reply_to_message_id"], 987654321)
        text = bot.calls[0][0][1]
        self.assertIn("89621", text)
        self.assertIn("2FA", text)
        self.assertIn("pass-123", text)

    def test_code_message_rejects_message_before_request(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(date=started_at - timedelta(seconds=10))

        self.assertFalse(_code_message_is_new(message, started_at))

    def test_code_message_accepts_message_at_request_start(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(date=started_at)

        self.assertTrue(_code_message_is_new(message, started_at))

    def test_code_message_rejects_old_message_outside_grace_window(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(date=started_at - timedelta(seconds=30))

        self.assertFalse(_code_message_is_new(message, started_at))

    def test_unauthorized_poll_error_is_terminal(self):
        self.assertEqual(_terminal_code_poll_status(RuntimeError("session is not authorized")), "unauthorized")
        self.assertIsNone(_terminal_code_poll_status(TimeoutError("temporary timeout")))

    async def test_poll_uses_newest_code_from_last_five_messages(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "account.session"
            session_path.write_bytes(b"session")
            account = SimpleNamespace(id=7, session_path=str(session_path))
            old = TelegramCodeMessage("11111", "telegram:10:0", started_at)
            newest = TelegramCodeMessage("22222", "telegram:11:0", started_at + timedelta(seconds=1))
            with patch("tglol.handlers.get_account", return_value=account), patch(
                "tglol.handlers._account_connection_params", return_value=(1, "hash", {})
            ), patch(
                "tglol.handlers.get_recent_telegram_codes", new=AsyncMock(return_value=[old, newest])
            ) as recent:
                _current, code, terminal = await _poll_new_telegram_code_message(
                    SimpleNamespace(), account, started_at=started_at, seen_keys=set()
                )
        self.assertFalse(terminal)
        self.assertEqual(code.code, "22222")
        self.assertEqual(recent.await_args.kwargs["limit"], 5)

    async def test_admin_card_code_includes_twofa(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "account.session"
            json_path = Path(tmpdir) / "account.json"
            session_path.write_bytes(b"session")
            json_path.write_text('{"twoFA":"pass-123"}', encoding="utf-8")
            account = SimpleNamespace(
                id=7, phone="+12536173293", session_path=str(session_path),
                twofa_password=None, json_original_path=str(json_path), json_effective_path=str(json_path),
            )
            callback = SimpleNamespace(
                data="account:telegram_code:7",
                message=SimpleNamespace(answer=AsyncMock()),
                answer=AsyncMock(),
            )
            with patch("tglol.handlers.get_account", return_value=account), patch(
                "tglol.handlers._account_connection_params", return_value=(1, "hash", {})
            ), patch("tglol.handlers.get_latest_telegram_code", new=AsyncMock(return_value="97479")):
                await _send_account_code(callback, SimpleNamespace())
        text = callback.message.answer.await_args.args[0]
        self.assertIn("97479", text)
        self.assertIn("12536173293", text)
        self.assertIn("2FA", text)
        self.assertIn("pass-123", text)


if __name__ == "__main__":
    unittest.main()
