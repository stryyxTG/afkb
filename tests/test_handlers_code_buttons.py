import unittest
from types import SimpleNamespace

from tglol.handlers import _send_code_delivery, _terminal_code_poll_status


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



    def test_unauthorized_poll_error_is_terminal(self):
        self.assertEqual(_terminal_code_poll_status(RuntimeError("session is not authorized")), "unauthorized")
        self.assertIsNone(_terminal_code_poll_status(TimeoutError("temporary timeout")))


if __name__ == "__main__":
    unittest.main()