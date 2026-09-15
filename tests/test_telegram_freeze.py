import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import AuthKeyUnregisteredError, FloodWaitError, SessionPasswordNeededError, UserDeactivatedBanError, UserDeactivatedError

from tglol.telegram_service import check_account_freeze, inspect_session_with_freeze_check


class TelegramFreezeInspectTests(unittest.IsolatedAsyncioTestCase):
    async def inspect_with_client(self, client):
        with patch("tglol.telegram_service.client_for", return_value=client):
            return await inspect_session_with_freeze_check(Path("account.session"), 123, "hash", {})

    async def test_user_deactivated_ban_from_authorization_is_frozen(self):
        client = AsyncMock()
        client.is_user_authorized.side_effect = UserDeactivatedBanError(None)

        result = await self.inspect_with_client(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(result["account_status"], "frozen")
        self.assertEqual(result["reason"], "user_deactivated_ban")
        client.disconnect.assert_awaited_once()

    async def test_user_deactivated_from_connect_is_frozen(self):
        client = AsyncMock()
        client.connect.side_effect = UserDeactivatedError(None)

        result = await self.inspect_with_client(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(result["account_status"], "frozen")
        self.assertEqual(result["reason"], "user_deactivated")
        client.disconnect.assert_awaited_once()

    async def test_auth_key_unregistered_from_authorization_is_dead(self):
        client = AsyncMock()
        client.is_user_authorized.side_effect = AuthKeyUnregisteredError(None)

        result = await self.inspect_with_client(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "dead")
        self.assertEqual(result["account_status"], "dead")
        self.assertEqual(result["reason"], "auth_key_unregistered")

    async def test_session_password_needed_from_authorization_is_need_2fa(self):
        client = AsyncMock()
        client.is_user_authorized.side_effect = SessionPasswordNeededError(None)

        result = await self.inspect_with_client(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "need_2fa")
        self.assertEqual(result["account_status"], "need_2fa")
        self.assertEqual(result["reason"], "session_password_needed")

    async def test_flood_wait_from_authorization_is_skipped(self):
        client = AsyncMock()
        client.is_user_authorized.side_effect = FloodWaitError(None, capture=42)

        result = await self.inspect_with_client(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["account_status"], "skipped")
        self.assertEqual(result["reason"], "flood_wait_42")



    async def test_deleted_user_flag_is_frozen(self):
        client = AsyncMock()
        client.get_me.return_value = SimpleNamespace(id=123, deleted=True)

        result = await check_account_freeze(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(result["reason"], "user_deleted_flag")
        client.get_dialogs.assert_not_called()

    async def test_restricted_user_flag_is_frozen(self):
        client = AsyncMock()
        client.get_me.return_value = SimpleNamespace(id=123, deleted=False, restricted=True, restriction_reason=[])

        result = await check_account_freeze(client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(result["reason"], "user_restricted_flag")
        client.get_dialogs.assert_not_called()

if __name__ == "__main__":
    unittest.main()