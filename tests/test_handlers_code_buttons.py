import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tglol.handlers import _code_message_is_new, _send_code_delivery, _terminal_code_poll_status


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))


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

        text = bot.calls[0][0][1]
        self.assertIn("89621", text)
        self.assertIn("2FA", text)
        self.assertIn("pass-123", text)

    def test_code_message_accepts_small_clock_skew(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(date=started_at - timedelta(seconds=90))

        self.assertTrue(_code_message_is_new(message, started_at))

    def test_code_message_rejects_old_message_outside_grace_window(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(date=started_at - timedelta(minutes=5))

        self.assertFalse(_code_message_is_new(message, started_at))

    def test_unauthorized_poll_error_is_terminal(self):
        self.assertEqual(_terminal_code_poll_status(RuntimeError("session is not authorized")), "unauthorized")
        self.assertIsNone(_terminal_code_poll_status(TimeoutError("temporary timeout")))


if __name__ == "__main__":
    unittest.main()