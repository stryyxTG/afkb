import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tglol.config import set_admin_ids
from tglol.worker_admin import admins_listing, grant_admin
import test_workers


class AdminAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = test_workers.AccountClaimTests()
        self.case.setUp()
        self.config = self.case.config

    def tearDown(self):
        self.case.tearDown()

    def test_set_admin_ids_updates_live_config_and_persists(self):
        with patch("tglol.config._write_env_value") as write_env:
            set_admin_ids(self.config, {1, 2})
        self.assertEqual(self.config.admin_ids, frozenset({1, 2}))
        write_env.assert_called_once_with("ADMIN_IDS", "1,2")

    async def test_grant_admin_by_id(self):
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock())
        with patch("tglol.config._write_env_value"):
            await grant_admin(message, state, self.config, "2")
        self.assertIn(2, self.config.admin_ids)
        state.clear.assert_awaited_once()
        self.assertIn("2", message.answer.await_args.args[0])

    async def test_grant_admin_rejects_bad_id(self):
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock())
        await grant_admin(message, state, self.config, "bad")
        self.assertEqual(self.config.admin_ids, frozenset({1}))
        state.clear.assert_not_awaited()

    def test_admin_listing_has_remove_buttons(self):
        with patch("tglol.config._write_env_value"):
            set_admin_ids(self.config, {1, 2})
        text, markup = admins_listing(self.config)
        self.assertIn("<code>1</code>", text)
        self.assertIn("<code>2</code>", text)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admins:remove_ask:1")


if __name__ == "__main__":
    unittest.main()
