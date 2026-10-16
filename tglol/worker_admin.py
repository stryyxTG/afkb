from html import escape

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tglol.config import Config, set_admin_ids
from tglol.db import (
    clear_worker_activity, get_worker, list_workers, revoke_worker_access,
    save_worker, worker_activity_stats, list_worker_deliveries, issued_activity_count,
)
from tglol.handlers import _parse_telegram_user_id, _resolve_worker_identity
from tglol.states import AddAdmin, AddWorker

router = Router(name="worker_admin")
PAGE_SIZE = 10


class AdminOnly(Filter):
    async def __call__(self, event, config: Config) -> bool:
        return bool(event.from_user and event.from_user.id in config.admin_ids)


router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())


def keyboard(*buttons):
    builder = InlineKeyboardBuilder()
    for label, data in buttons:
        builder.button(text=label, callback_data=data)
    builder.adjust(1)
    return builder.as_markup()


def menu():
    return keyboard(
        ("Список воркеров", "workers:list:0"),
        ("Добавить по ID", "workers:add"),
        ("\u0421\u043a\u0430\u0447\u0430\u0442\u044c \u0432\u044b\u0434\u0430\u0447\u0438 TXT", "workers:report"),
        ("Сбросить всю статистику", "workers:reset_ask:all"),
        ("Назад", "accounts:menu"),
    )


def admins_menu():
    return keyboard(
        ("Список администраторов", "admins:list"),
        ("Выдать доступ по ID", "admins:add"),
        ("Назад", "accounts:menu"),
    )


def worker_label(worker):
    name = " ".join(part for part in (worker.first_name, worker.last_name) if part)
    return f"@{worker.username}" if worker.username else name or "Без имени"


def stats_text(stats):
    return (
        f"Запросов: <b>{stats.get('requests', 0)}</b>\n"
        f"Запрошено аккаунтов: <b>{stats.get('requested', 0)}</b>\n"
        f"Аккаунтов с отправленным кодом: <b>{stats.get('delivered', 0)}</b>"
    )


def listing(config: Config, page: int = 0, *, statistics: bool = False):
    if statistics and not config.workers_enabled:
        return (
            f"<b>\u0412\u044b\u0434\u0430\u0447\u0438</b>\n\u0412\u044b\u0434\u0430\u043d\u043e \u0441 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0433\u043e \u0441\u0431\u0440\u043e\u0441\u0430: <b>{issued_activity_count(config)}</b>",
            keyboard(("\u041c\u0435\u043d\u044e \u0432\u043e\u0440\u043a\u0435\u0440\u043e\u0432", "workers:menu")),
        )
    workers = list_workers(config)
    pages = max(1, (len(workers) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    stats = worker_activity_stats(config) if statistics else {}
    selected = workers[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    title = "Статистика воркеров" if statistics else "Воркеры"
    lines = [f"<b>{title}</b> · {len(workers)}", f"Страница {page + 1}/{pages}"]
    buttons = []
    for worker in selected:
        buttons.append((f"{worker_label(worker)[:40]} · {worker.user_id}", f"workers:open:{worker.user_id}"))
        if statistics:
            lines.append(f"\n{escape(worker_label(worker))} · <code>{worker.user_id}</code>\n"
                         + stats_text(stats.get(worker.user_id, {})))
    if not workers:
        lines.append("Список пуст. Добавь воркера по Telegram ID.")
    prefix = "stats" if statistics else "list"
    if page:
        buttons.append(("‹ Назад", f"workers:{prefix}:{page - 1}"))
    if page + 1 < pages:
        buttons.append(("Вперёд ›", f"workers:{prefix}:{page + 1}"))
    buttons.append(("Меню воркеров", "workers:menu"))
    return "\n".join(lines), keyboard(*buttons)


def admins_listing(config: Config):
    admin_ids = sorted(config.admin_ids)
    lines = [f"<b>Администраторы</b> · {len(admin_ids)}"]
    buttons = []
    for user_id in admin_ids:
        lines.append(f"• <code>{user_id}</code>")
        buttons.append((f"Убрать {user_id}", f"admins:remove_ask:{user_id}"))
    buttons.append(("Меню администраторов", "admins:menu"))
    return "\n".join(lines), keyboard(*buttons)


def _delivery_worker_label(delivery) -> str:
    name = " ".join(part for part in (delivery.first_name, delivery.last_name) if part)
    return f"@{delivery.username}" if delivery.username else name or "\u0411\u0435\u0437 \u0438\u043c\u0435\u043d\u0438"


def delivery_report_text(config: Config, user_id: int | None = None) -> str:
    deliveries = list_worker_deliveries(config, user_id)
    if not deliveries:
        return ""
    lines = ["\u0420\u0435\u0430\u043b\u044c\u043d\u043e \u0432\u044b\u0434\u0430\u043d\u043d\u044b\u0435 \u043d\u043e\u043c\u0435\u0440\u0430", ""]
    for item in deliveries:
        lines.append(f"{item.phone or '-'} | {_delivery_worker_label(item)}")
    return "\n".join(lines) + "\n"


async def send_delivery_report(message: Message, config: Config, user_id: int | None = None) -> None:
    if not config.workers_enabled:
        count = issued_activity_count(config)
        await message.answer(
            f"\u0412\u044b\u0434\u0430\u043d\u043e \u0441 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0433\u043e \u0441\u0431\u0440\u043e\u0441\u0430: <b>{count}</b>",
            parse_mode="HTML",
        )
        return
    report = delivery_report_text(config, user_id)
    if not report:
        await message.answer("\u0412 \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0435 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0440\u0435\u0430\u043b\u044c\u043d\u043e \u0432\u044b\u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u043e\u043c\u0435\u0440\u043e\u0432.")
        return
    filename = "issued_accounts.txt" if user_id is None else f"issued_accounts_{user_id}.txt"
    await message.answer_document(
        BufferedInputFile(report.encode("utf-8"), filename=filename),
        caption="\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u0440\u0435\u0430\u043b\u044c\u043d\u043e \u0432\u044b\u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u043e\u043c\u0435\u0440\u043e\u0432.",
    )


def reset_prompt(target):
    scope = "всех воркеров" if target == "all" else "этого воркера"
    return (
        f"Сбросить статистику {scope}? Доступ и аккаунты сохранятся.",
        keyboard(("Да, сбросить", f"workers:reset_confirm:{target}"),
                 ("Отмена", "workers:menu")),
    )


@router.message(Command("admins"))
async def admins_command(message: Message, state: FSMContext, config: Config):
    await state.clear()
    raw_id = (message.text or "").partition(" ")[2].strip()
    if raw_id:
        await grant_admin(message, state, config, raw_id)
        return
    await message.answer("Администраторы", reply_markup=admins_menu())


@router.callback_query(F.data == "admins:menu")
async def show_admins_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Администраторы", reply_markup=admins_menu())


@router.callback_query(F.data == "admins:list")
async def show_admins_list(callback: CallbackQuery, config: Config):
    text, markup = admins_listing(config)
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "admins:add")
async def ask_admin(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddAdmin.waiting_user_id)
    await callback.answer()
    await callback.message.edit_text(
        "Отправь числовой Telegram ID нового администратора.",
        reply_markup=keyboard(("Отмена", "admins:menu")),
    )


async def grant_admin(message: Message, state: FSMContext, config: Config, raw_id: str) -> None:
    user_id = _parse_telegram_user_id(raw_id)
    if user_id is None:
        await message.answer("Нужен положительный числовой Telegram ID. Для отмены: /cancel.")
        return
    if user_id in config.admin_ids:
        await state.clear()
        await message.answer("Этот пользователь уже администратор.", reply_markup=admins_menu())
        return
    set_admin_ids(config, set(config.admin_ids) | {user_id})
    await state.clear()
    await message.answer(
        f"Доступ администратора выдан: <code>{user_id}</code>. ID сохранён в .env.",
        reply_markup=admins_menu(), parse_mode="HTML",
    )


@router.message(AddAdmin.waiting_user_id, ~F.text.startswith("/"))
async def add_admin(message: Message, state: FSMContext, config: Config):
    await grant_admin(message, state, config, message.text or "")


@router.callback_query(F.data.startswith("admins:remove_ask:"))
@router.callback_query(F.data.startswith("admins:remove_confirm:"))
async def remove_admin(callback: CallbackQuery, config: Config):
    _, action, raw_id = callback.data.split(":")
    user_id = _parse_telegram_user_id(raw_id)
    if user_id is None or user_id not in config.admin_ids:
        await callback.answer("Администратор не найден.", show_alert=True)
        return
    if action == "remove_ask":
        if len(config.admin_ids) == 1:
            await callback.answer("Нельзя удалить последнего администратора.", show_alert=True)
            return
        await callback.answer()
        await callback.message.edit_text(
            f"Забрать доступ администратора у <code>{user_id}</code>?",
            reply_markup=keyboard(
                ("Да, забрать доступ", f"admins:remove_confirm:{user_id}"),
                ("Отмена", "admins:list"),
            ), parse_mode="HTML",
        )
        return
    if callback.from_user.id == user_id:
        await callback.answer("Свой доступ нельзя отозвать из этого меню.", show_alert=True)
        return
    set_admin_ids(config, set(config.admin_ids) - {user_id})
    await callback.answer("Доступ администратора отозван.")
    text, markup = admins_listing(config)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("workers"))
async def workers_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Воркеры", reply_markup=menu())


@router.message(Command("stats"))
async def stats_command(message: Message, state: FSMContext, config: Config):
    await state.clear()
    await send_delivery_report(message, config)


@router.message(Command("reset"))
async def reset_command(message: Message, state: FSMContext):
    await state.clear()
    text, markup = reset_prompt("all")
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "workers:menu")
async def show_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Воркеры", reply_markup=menu())


@router.callback_query(F.data == "workers:add")
async def ask_worker(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddWorker.waiting_user_id)
    await callback.answer()
    await callback.message.edit_text(
        "Отправь числовой Telegram ID воркера.",
        reply_markup=keyboard(("Отмена", "workers:menu")),
    )


@router.message(AddWorker.waiting_user_id, ~F.text.startswith("/"))
async def add_worker(message: Message, state: FSMContext, config: Config, bot):
    user_id = _parse_telegram_user_id(message.text or "")
    if user_id is None:
        await message.answer("Нужен положительный числовой Telegram ID. Для отмены: /cancel.")
        return
    if get_worker(config, user_id):
        await state.clear()
        await message.answer("Этот воркер уже в списке.", reply_markup=menu())
        return
    first_name, last_name, username = await _resolve_worker_identity(bot, config, user_id)
    save_worker(config, user_id=user_id, first_name=first_name, last_name=last_name,
                username=username, remaining_limit=0, created_by=message.from_user.id)
    await state.clear()
    label = f"@{username}" if username else first_name
    note = "" if username else "\nЕсли ник пока недоступен, он обновится при обращении воркера к боту."
    await message.answer(
        f"Доступ выдан: {escape(label)} · <code>{user_id}</code>{note}",
        reply_markup=menu(), parse_mode="HTML",
    )


@router.callback_query(F.data == "workers:report")
@router.callback_query(F.data.startswith("workers:report:"))
async def download_delivery_report(callback: CallbackQuery, config: Config) -> None:
    raw_user_id = callback.data.partition(":report:")[2]
    user_id = _parse_telegram_user_id(raw_user_id) if raw_user_id else None
    if raw_user_id and user_id is None:
        await callback.answer("\u0412\u043e\u0440\u043a\u0435\u0440 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", show_alert=True)
        return
    await callback.answer()
    await send_delivery_report(callback.message, config, user_id)


@router.callback_query(F.data.startswith("workers:list:"))
@router.callback_query(F.data.startswith("workers:stats:"))
async def show_list(callback: CallbackQuery, config: Config, state: FSMContext):
    await state.clear()
    _, action, raw_page = callback.data.split(":")
    try:
        page = int(raw_page)
    except ValueError:
        await callback.answer("Некорректная страница.")
        return
    text, markup = listing(config, page, statistics=action == "stats")
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("workers:open:"))
async def show_worker(callback: CallbackQuery, config: Config):
    raw = callback.data.rsplit(":", 1)[-1]
    user_id = _parse_telegram_user_id(raw)
    worker = get_worker(config, user_id) if user_id else None
    if not worker:
        await callback.answer("\u0412\u043e\u0440\u043a\u0435\u0440 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", show_alert=True)
        return
    delivered_count = len(list_worker_deliveries(config, user_id))
    statistic = (
        f"\u0412\u044b\u0434\u0430\u043d\u043e \u0441 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0433\u043e \u0441\u0431\u0440\u043e\u0441\u0430: <b>{issued_activity_count(config)}</b>"
        if not config.workers_enabled
        else f"\u0420\u0435\u0430\u043b\u044c\u043d\u043e \u0432\u044b\u0434\u0430\u043d\u043e: <b>{delivered_count}</b>"
    )
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{escape(worker_label(worker))}</b>\nID: <code>{user_id}</code>\n\n{statistic}",
        reply_markup=keyboard(
            ("\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u0432\u044b\u0434\u0430\u0447", f"workers:report:{user_id}"),
            ("\u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0443", f"workers:reset_ask:{user_id}"),
            ("\u0417\u0430\u0431\u0440\u0430\u0442\u044c \u0434\u043e\u0441\u0442\u0443\u043f", f"workers:revoke_ask:{user_id}"),
            ("\u041d\u0430\u0437\u0430\u0434", "workers:list:0"),
        ), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("workers:reset_ask:"))
@router.callback_query(F.data.startswith("workers:reset_confirm:"))
@router.callback_query(F.data.startswith("workers:revoke_ask:"))
@router.callback_query(F.data.startswith("workers:revoke_confirm:"))
async def manage_worker(callback: CallbackQuery, config: Config):
    _, action, target = callback.data.split(":")
    user_id = _parse_telegram_user_id(target)
    if target != "all" and (user_id is None or get_worker(config, user_id) is None):
        await callback.answer("Воркер не найден.", show_alert=True)
        return
    if target == "all" and action.startswith("revoke"):
        await callback.answer("Некорректный запрос.")
        return
    if action == "reset_ask":
        text, markup = reset_prompt(target)
    elif action == "reset_confirm":
        clear_worker_activity(config, user_id)
        text, markup = "Статистика сброшена. Доступ и аккаунты сохранены.", menu()
    elif action == "revoke_ask":
        worker = get_worker(config, user_id)
        text = f"Забрать доступ у {escape(worker_label(worker))} ({user_id})? Его статистика будет удалена."
        markup = keyboard(("Да, забрать доступ", f"workers:revoke_confirm:{user_id}"),
                          ("Отмена", f"workers:open:{user_id}"))
    else:
        revoke_worker_access(config, user_id)
        text, markup = "Доступ воркера отозван.", menu()
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
