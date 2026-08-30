import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tglol.handlers import _send_code_delivery, _terminal_code_poll_status


class FakeBot:
    pass


class CodeButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_code_delivery_uses_receiver_and_keeps_reply(self):
        bot = FakeBot()
        config = SimpleNamespace()
        account = SimpleNamespace(
            id=1234567890,
            phone="+12536173293",
            twofa_password=None,
            json_original_path=None,
            json_effective_path=None,
        )
        calls = []

        async def fake_send(*args, **kwargs):
            calls.append((args, kwargs))
            return 321

        with patch("tglol.handlers._send_via_code_receiver", fake_send):
            await _send_code_delivery(
                bot,
                chat_id=-1001234567890123,
                account=account,
                code="89621",
                requester_user_id=1234567890123,
                config=config,
                reply_to_message_id=987654321,
            )

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertIs(args[0], config)
        self.assertEqual(kwargs["chat_id"], -1001234567890123)
        self.assertEqual(kwargs["reply_to_message_id"], 987654321)
        self.assertIn("12536173293", kwargs["text"])
        self.assertIn("89621", kwargs["text"])

    async def test_code_delivery_does_not_expose_twofa(self):
        bot = FakeBot()
        config = SimpleNamespace()
        account = SimpleNamespace(
            id=1,
            phone="+12536173293",
            twofa_password="secret-password",
            json_original_path=None,
            json_effective_path=None,
        )
        calls = []

        async def fake_send(*args, **kwargs):
            calls.append((args, kwargs))
            return 321

        with patch("tglol.handlers._send_via_code_receiver", fake_send):
            await _send_code_delivery(
                bot,
                chat_id=-1001,
                account=account,
                code="89621",
                requester_user_id=123,
                config=config,
            )

        self.assertNotIn("secret-password", calls[0][1]["text"])
        self.assertNotIn("2FA", calls[0][1]["text"])

    def test_unauthorized_poll_error_is_terminal(self):
        self.assertEqual(_terminal_code_poll_status(RuntimeError("session is not authorized")), "unauthorized")
        self.assertIsNone(_terminal_code_poll_status(TimeoutError("temporary timeout")))


if __name__ == "__main__":
    unittest.main()
