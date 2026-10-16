import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, User
from datetime import datetime, timezone

import test_workers
from tglol.db import (
    clear_worker_activity, get_account, get_worker, init_db, record_issue_delivery, record_worker_delivery,
    record_worker_request, revoke_worker_access, save_worker, worker_activity_stats, list_worker_deliveries,
    issued_activity_count,
)
from tglol.handlers import AccessMiddleware, _give_out_accounts
from tglol.worker_admin import (
    AdminOnly, add_worker, delivery_report_text, listing, menu, send_delivery_report, router as worker_router,
)


class WorkerAccessTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_workers.AccountClaimTests.setUp
    tearDown = test_workers.AccountClaimTests.tearDown
    add_account = test_workers.AccountClaimTests.add_account

    def grant(self, user_id=2, username="worker"):
        save_worker(self.config, user_id=user_id, first_name="Worker", last_name=None,
                    username=username, remaining_limit=0, created_by=1)

    def message(self, user_id=2, chat_id=-100123, text="тг 10"):
        return Message(message_id=10, date=datetime.now(timezone.utc),
                       chat=Chat(id=chat_id, type="supergroup" if chat_id < 0 else "private"),
                       from_user=User(id=user_id, is_bot=False, first_name="Current", username="new_nick"),
                       text=text)

    async def test_trigger_requires_worker_and_admin_bypasses_list(self):
        middleware = AccessMiddleware()
        handler = AsyncMock()
        for user_id in (2, 1):
            event = self.message(user_id)
            await middleware(handler, event, {"config": self.config, "event_from_user": event.from_user})
        self.assertEqual(handler.await_count, 1)
        self.assertEqual(handler.call_args.args[0].from_user.id, 1)
        self.grant()
        event = self.message()
        await middleware(handler, event, {"config": self.config, "event_from_user": event.from_user})
        self.assertEqual(handler.await_count, 2)
        self.assertEqual(get_worker(self.config, 2).username, "new_nick")
        revoke_worker_access(self.config, 2)
        await middleware(handler, event, {"config": self.config, "event_from_user": event.from_user})
        self.assertEqual(handler.await_count, 2)

    async def test_trigger_allows_any_chat_member_when_workers_disabled(self):
        object.__setattr__(self.config, "workers_enabled", False)
        middleware = AccessMiddleware()
        handler = AsyncMock()
        event = self.message(user_id=2)
        await middleware(handler, event, {"config": self.config, "event_from_user": event.from_user})
        handler.assert_awaited_once()

    async def test_disabled_workers_do_not_record_worker_request(self):
        object.__setattr__(self.config, "workers_enabled", False)
        message = SimpleNamespace(from_user=SimpleNamespace(id=2), text="?? 10", answer=AsyncMock())
        await _give_out_accounts(message, SimpleNamespace(), self.config)
        self.assertEqual(worker_activity_stats(self.config), {})
        message.answer.assert_awaited_once()

    async def test_disabled_workers_report_only_global_issue_count(self):
        account_id = self.add_account("40001")
        record_issue_delivery(self.config, account_id)
        object.__setattr__(self.config, "workers_enabled", False)
        message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())
        await send_delivery_report(message, self.config)
        self.assertIn("1", message.answer.await_args.args[0])
        message.answer_document.assert_not_awaited()
        clear_worker_activity(self.config)
        self.assertEqual(issued_activity_count(self.config), 0)

    async def test_worker_cannot_use_admin_menu_or_other_chat(self):
        self.grant()
        middleware = AccessMiddleware()
        handler = AsyncMock()
        for event in (self.message(text="/workers"), self.message(chat_id=-100999)):
            await middleware(handler, event, {"config": self.config, "event_from_user": event.from_user})
        handler.assert_not_awaited()
        self.assertFalse(await AdminOnly()(self.message(), self.config))
        self.assertTrue(await AdminOnly()(self.message(1), self.config))

    async def test_retry_button_requires_current_worker_access(self):
        self.grant()
        event = CallbackQuery(id="test", from_user=self.message().from_user,
                              chat_instance="test", message=self.message(),
                              data="arc:1:-100123:2:10")
        handler = AsyncMock()
        await AccessMiddleware()(handler, event, {"config": self.config, "event_from_user": event.from_user})
        handler.assert_awaited_once()
        from unittest.mock import patch
        revoke_worker_access(self.config, 2)
        with patch.object(CallbackQuery, "answer", new=AsyncMock()) as answer:
            await AccessMiddleware()(handler, event, {"config": self.config, "event_from_user": event.from_user})
            answer.assert_awaited_once()
        self.assertEqual(handler.await_count, 1)

    async def test_empty_stock_request_is_counted_once(self):
        self.grant()
        message = SimpleNamespace(from_user=SimpleNamespace(id=2), text="тг 10", answer=AsyncMock())
        await _give_out_accounts(message, SimpleNamespace(), self.config)
        stats = worker_activity_stats(self.config)[2]
        self.assertEqual((stats["requests"], stats["requested"], stats["delivered"]), (1, 10, 0))
        message.answer.assert_awaited_once()

    async def test_direct_giveout_denies_unlisted_user(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=2), text="тг 10", answer=AsyncMock())
        await _give_out_accounts(message, SimpleNamespace(), self.config)
        self.assertEqual(worker_activity_stats(self.config), {})
        message.answer.assert_not_awaited()

    def test_delivery_report_contains_only_delivered_phone_and_requester(self):
        self.grant(2, "requested_by")
        account_id = self.add_account("40001")
        request_id = record_worker_request(self.config, 2, 1)
        record_worker_delivery(self.config, request_id, get_account(self.config, account_id))
        deliveries = list_worker_deliveries(self.config)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual((deliveries[0].phone, deliveries[0].worker_id), ("40001", 2))
        report = delivery_report_text(self.config)
        self.assertIn("40001", report)
        self.assertIn("@requested_by", report)
        clear_worker_activity(self.config)
        self.assertEqual(list_worker_deliveries(self.config), [])
        callbacks = [button.callback_data for row in menu().inline_keyboard for button in row]
        self.assertIn("workers:report", callbacks)

    def test_reset_is_scoped_and_late_delivery_cannot_restore_old_stats(self):
        self.grant(2)
        self.grant(3)
        account_id = self.add_account("40001")
        first = record_worker_request(self.config, 2, 10)
        second = record_worker_request(self.config, 3, 2)
        record_worker_delivery(self.config, first)
        record_worker_delivery(self.config, second)
        clear_worker_activity(self.config, 2)
        new_request = record_worker_request(self.config, 2, 4)
        self.assertNotEqual(first, new_request)
        record_worker_delivery(self.config, first)
        stats = worker_activity_stats(self.config)
        self.assertEqual((stats[2]["requested"], stats[2]["delivered"]), (4, 0))
        self.assertEqual(stats[3]["delivered"], 1)
        clear_worker_activity(self.config)
        self.assertEqual(worker_activity_stats(self.config), {})
        self.assertIsNotNone(get_worker(self.config, 2))
        self.assertIsNotNone(get_account(self.config, account_id))

    def test_username_in_list_and_stats_escape_html(self):
        self.grant(username="test_worker")
        text, markup = listing(self.config)
        self.assertIn("@test_worker", markup.inline_keyboard[0][0].text)
        self.grant(username="<bad>")
        text, _ = listing(self.config, statistics=True)
        self.assertIn("&lt;bad&gt;", text)
        self.assertNotIn("<bad>", text)

    async def test_add_by_id_resolves_identity_and_grants_access(self):
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(text="2", from_user=SimpleNamespace(id=1), answer=AsyncMock())
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
            status="member", user=SimpleNamespace(first_name="Name", last_name=None, username="nickname")
        )))
        await add_worker(message, state, self.config, bot)
        worker = get_worker(self.config, 2)
        self.assertEqual(worker.username, "nickname")
        self.assertEqual(worker.remaining_limit, 0)
        state.clear.assert_awaited_once()

    async def test_router_blocks_worker_admin_callback(self):
        from unittest.mock import patch
        self.grant()
        request = record_worker_request(self.config, 2, 10)
        event = CallbackQuery(id="test", from_user=self.message().from_user,
                              chat_instance="test", message=self.message(),
                              data="workers:reset_confirm:all")
        with patch.object(CallbackQuery, "answer", new=AsyncMock()), patch.object(
            Message, "edit_text", new=AsyncMock()
        ):
            await worker_router.propagate_event("callback_query", event, config=self.config)
            self.assertEqual(worker_activity_stats(self.config)[2]["requested"], 10)
            admin_event = event.model_copy(update={"from_user": self.message(1).from_user})
            await worker_router.propagate_event("callback_query", admin_event, config=self.config)
            self.assertEqual(worker_activity_stats(self.config), {})

    def test_legacy_workers_migrate_and_restart_preserves_stats(self):
        connection = sqlite3.connect(self.config.db_path)
        connection.execute("DROP TABLE workers")
        connection.execute("CREATE TABLE workers (telegram_id INTEGER, name TEXT)")
        connection.execute("INSERT INTO workers VALUES (2, 'Legacy')")
        connection.commit()
        connection.close()
        init_db(self.config)
        self.assertEqual(get_worker(self.config, 2).first_name, "Legacy")
        request = record_worker_request(self.config, 2, 10)
        record_worker_delivery(self.config, request)
        init_db(self.config)
        self.assertEqual(worker_activity_stats(self.config)[2]["requested"], 10)


if __name__ == "__main__":
    unittest.main()
