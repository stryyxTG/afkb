from __future__ import annotations

from collections.abc import Sequence
from math import ceil

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tglol.registration import (
    REGISTRATION_SERVICES,
    parse_reg_origin,
    reg_origin,
    service_filter_label,
    service_filter_value,
    service_label,
    services_label,
    services_short_label,
)


ACCOUNTS_PER_PAGE = 14
WORKERS_PER_PAGE = 20


def accounts_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить аккаунт", callback_data="accounts:add")
    builder.button(text="Хранилище", callback_data="accounts:common_sections")
    builder.button(text="\u041f\u0440\u043e\u043a\u0441\u0438", callback_data="proxy:menu")
    builder.button(text="\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0432\u044b\u0434\u0430\u0447\u0438", callback_data="trigger:menu")
    builder.adjust(1)
    return builder.as_markup()


def trigger_settings_menu(*, has_chat: bool, has_code_receiver: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="\u0427\u0430\u0442 \u0432\u044b\u0434\u0430\u0447\u0438", callback_data="trigger:set_chat")
    builder.button(text="\u0422\u0440\u0438\u0433\u0433\u0435\u0440\u043d\u043e\u0435 \u0441\u043b\u043e\u0432\u043e", callback_data="trigger:set_word")
    builder.button(text="\u041f\u043e\u0441\u0440\u0435\u0434\u043d\u0438\u043a: \u043f\u043e \u043d\u043e\u043c\u0435\u0440\u0443", callback_data="receiver:add:code")
    builder.button(text="\u041f\u043e\u0441\u0440\u0435\u0434\u043d\u0438\u043a: ZIP session/json", callback_data="receiver:add:zip")
    if has_code_receiver:
        builder.button(text="\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043f\u043e\u0441\u0440\u0435\u0434\u043d\u0438\u043a\u0430", callback_data="receiver:check")
        builder.button(text="\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u043e\u0441\u0440\u0435\u0434\u043d\u0438\u043a\u0430", callback_data="receiver:delete")
    if has_chat:
        builder.button(text="\u041e\u0447\u0438\u0441\u0442\u0438\u0442\u044c \u0447\u0430\u0442", callback_data="trigger:clear_chat")
    builder.button(text="\u041d\u0430\u0437\u0430\u0434", callback_data="accounts:menu")
    builder.adjust(1)
    return builder.as_markup()


def proxy_menu(*, has_proxy: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c / \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c", callback_data="proxy:set")
    builder.button(text="\u0427\u0435\u043a\u043d\u0443\u0442\u044c \u043f\u0438\u043d\u0433", callback_data="proxy:check")
    if has_proxy:
        builder.button(text="\u0423\u0434\u0430\u043b\u0438\u0442\u044c", callback_data="proxy:delete")
    builder.button(text="\u041d\u0430\u0437\u0430\u0434", callback_data="accounts:menu")
    builder.adjust(1)
    return builder.as_markup()


def add_account_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="По номеру + секретное число", callback_data="accounts:add:code")
    builder.button(text="Массово .zip", callback_data="accounts:add:zip")
    builder.button(text="Назад", callback_data="accounts:menu")
    builder.adjust(1)
    return builder.as_markup()


def digit_code_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for digit in "123456789":
        builder.button(text=digit, callback_data=f"code:digit:{digit}")
    builder.button(text="Очистить", callback_data="code:clear")
    builder.button(text="0", callback_data="code:digit:0")
    builder.button(text="Стереть", callback_data="code:backspace")
    builder.button(text="Подтвердить", callback_data="code:done")
    builder.button(text="Отмена", callback_data="accounts:add")
    builder.adjust(3, 3, 3, 3, 1, 1)
    return builder.as_markup()


def _account_label(account) -> str:
    return account.phone or account.username or str(account.telegram_user_id or "без данных")


def _origin_stage(origin: str) -> str | None:
    if origin in {"common", "common_clean"}:
        return "clean"
    if origin == "common_issued":
        return "issued"
    return None


def _origin_to_stage_filter(origin: str) -> tuple[str, str]:
    if origin in {"common", "common_clean"}:
        return "clean", "all"
    if origin == "common_issued":
        return "issued", "all"
    return "clean", "all"


def accounts_page_keyboard(
    accounts: Sequence,
    *,
    total: int,
    page: int,
    origin: str,
    ref_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for account in accounts:
        builder.button(
            text=_account_label(account),
            callback_data=f"account:open:{account.id}:{origin}:{ref_id}:{page}",
        )

    if origin.startswith("common"):
        stage, filter_value = _origin_to_stage_filter(origin)
        builder.button(
            text="Скачать ZIP",
            callback_data=f"accounts:zip_common:{stage}:{filter_value}",
        )
        builder.button(
            text="Удалить всё",
            callback_data=f"accounts:delete_common_ask:{stage}:{filter_value}",
        )

    pages = max(1, ceil(total / ACCOUNTS_PER_PAGE))
    nav_count = 0
    if page > 0:
        builder.button(text="‹ Назад", callback_data=f"accounts:page:{origin}:{ref_id}:{page - 1}")
        nav_count += 1
    builder.button(text=f"{page + 1}/{pages}", callback_data="noop")
    nav_count += 1
    if page + 1 < pages:
        builder.button(text="Вперед ›", callback_data=f"accounts:page:{origin}:{ref_id}:{page + 1}")
        nav_count += 1

    builder.button(text="Меню аккаунтов", callback_data="accounts:menu")
    if origin.startswith("common"):
        builder.adjust(*([1] * len(accounts)), 1, 1, nav_count, 1)
    else:
        builder.adjust(*([1] * len(accounts)), nav_count, 1)
    return builder.as_markup()


def account_detail_menu(
    account_id: int,
    *,
    account_stage: str = "nereg",
    origin: str,
    ref_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    back = f"accounts:page:{origin}:{ref_id}:{page}"
    builder = InlineKeyboardBuilder()
    builder.button(text="Проверить аккаунт", callback_data=f"account:check_ask:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Скопировать номер", callback_data=f"accounts:phone:{account_id}")
    builder.button(text="Получить секретное число", callback_data=f"account:telegram_code:{account_id}")
    builder.button(text="Скачать session", callback_data=f"accounts:file:session:{account_id}")
    builder.button(text="Скачать JSON", callback_data=f"accounts:file:json:{account_id}")
    builder.button(text="Удалить аккаунт", callback_data=f"account:delete_ask:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Назад", callback_data=back)
    builder.adjust(1)
    return builder.as_markup()


def common_storage_sections_menu(*, clean_count: int, issued_count: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Чистые · {clean_count}", callback_data="accounts:page:common_clean:0:0")
    builder.button(text=f"Выданные · {issued_count}", callback_data="accounts:page:common_issued:0:0")
    builder.button(text="Назад", callback_data="accounts:menu")
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete_account_menu(account_id: int, origin: str, ref_id: int, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="ДА, УДАЛИТЬ", callback_data=f"account:delete_confirm:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Отмена", callback_data=f"account:open:{account_id}:{origin}:{ref_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_check_account_menu(account_id: int, origin: str, ref_id: int, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, проверить", callback_data=f"account:check_confirm:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Назад", callback_data=f"account:open:{account_id}:{origin}:{ref_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete_common_stage_menu(
    stage: str,
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> InlineKeyboardMarkup:
    service_part = service_filter_value(registration_service, excluded_service)
    if stage == "clean":
        cancel_origin = "common_clean"
    elif stage == "issued":
        cancel_origin = "common_issued"
    else:
        cancel_origin = f"common_{stage}"
    builder = InlineKeyboardBuilder()
    builder.button(text="ДА, УДАЛИТЬ ВСЕ", callback_data=f"accounts:delete_common_confirm:{stage}:{service_part}")
    builder.button(text="Отмена", callback_data=f"accounts:page:{cancel_origin}:0:0")
    builder.adjust(1)
    return builder.as_markup()


def confirm_account_stage_menu(
    account_id: int,
    target_stage: str,
    registration_service: str | None,
    origin: str,
    ref_id: int,
    page: int,
    step: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    next_step = "ask2" if step == 1 else "confirm"
    label = "ДА, ПРОДОЛЖИТЬ" if step == 1 else "ДА, ПОДТВЕРЖДАЮ"
    builder.button(
        text=label,
        callback_data=(
            f"account:stage_{next_step}:{account_id}:{target_stage}:"
            f"{registration_service or 'none'}:{origin}:{ref_id}:{page}"
        ),
    )
    builder.button(text="Отмена", callback_data=f"account:open:{account_id}:{origin}:{ref_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def registration_service_menu(selected_services: Sequence[str]) -> InlineKeyboardMarkup:
    selected = set(selected_services)
    builder = InlineKeyboardBuilder()
    for service in REGISTRATION_SERVICES:
        builder.button(
            text=f"✓ {service_label(service)}" if service in selected else service_label(service),
            callback_data=f"account:service_toggle:{service}",
        )
    builder.button(text="Сохранить", callback_data="account:service_save")
    builder.button(text="Назад", callback_data="account:service_cancel")
    builder.adjust(1)
    return builder.as_markup()


def registration_filter_menu(current_origin: str) -> InlineKeyboardMarkup:
    current_filter = parse_reg_origin(current_origin) or (None, None)
    current_service, current_excluded = current_filter
    builder = InlineKeyboardBuilder()

    label = "• Все РЕГ" if current_filter == (None, None) else "Все РЕГ"
    builder.button(text=label, callback_data="accounts:page:common_reg:0:0")

    for service in REGISTRATION_SERVICES:
        include_label = service_label(service)
        exclude_label = f"не {service_label(service)}"
        if current_service == service:
            include_label = f"• {include_label}"
        if current_excluded == service:
            exclude_label = f"• {exclude_label}"
        builder.button(text=include_label, callback_data=f"accounts:page:{reg_origin(registration_service=service)}:0:0")
        builder.button(text=exclude_label, callback_data=f"accounts:page:{reg_origin(excluded_service=service)}:0:0")

    builder.button(text="Назад к списку", callback_data=f"accounts:page:{current_origin}:0:0")
    builder.adjust(1, 2, 2, 2, 1)
    return builder.as_markup()


def workers_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Список воркеров", callback_data="workers:list")
    builder.button(text="Выдать доступ", callback_data="workers:add")
    builder.button(text="Забрать доступ", callback_data="workers:remove")
    builder.adjust(1)
    return builder.as_markup()


def workers_list_menu(workers: Sequence, *, action: str = "open", page: int = 0) -> InlineKeyboardMarkup:
    pages = max(1, ceil(len(workers) / WORKERS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    page_workers = workers[page * WORKERS_PER_PAGE:(page + 1) * WORKERS_PER_PAGE]
    builder = InlineKeyboardBuilder()
    for worker in page_workers:
        full_name = " ".join(part for part in (worker.first_name, worker.last_name) if part).strip()
        label = f"{(full_name or 'Без имени')[:38]} · {worker.user_id}"
        callback_prefix = "workers:revoke_ask" if action == "remove" else "workers:open"
        builder.button(text=label, callback_data=f"{callback_prefix}:{worker.user_id}")
    page_prefix = "workers:remove_page" if action == "remove" else "workers:list_page"
    if page > 0:
        builder.button(text="‹ Назад", callback_data=f"{page_prefix}:{page - 1}")
    if pages > 1:
        builder.button(text=f"{page + 1}/{pages}", callback_data="noop")
    if page + 1 < pages:
        builder.button(text="Вперёд ›", callback_data=f"{page_prefix}:{page + 1}")
    builder.button(text="Назад", callback_data="workers:menu")
    builder.adjust(1)
    return builder.as_markup()


def worker_detail_menu(worker_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Выдать / изменить лимит", callback_data=f"workers:limit:{worker_id}")
    builder.button(text="Сбросить лимит", callback_data=f"workers:reset_ask:{worker_id}")
    builder.button(text="Забрать доступ", callback_data=f"workers:revoke_ask:{worker_id}")
    builder.button(text="Назад к списку", callback_data="workers:list")
    builder.adjust(1)
    return builder.as_markup()


def worker_reset_confirm_menu(worker_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, сбросить лимит", callback_data=f"workers:reset_confirm:{worker_id}")
    builder.button(text="Отмена", callback_data=f"workers:open:{worker_id}")
    builder.adjust(1)
    return builder.as_markup()


def worker_revoke_confirm_menu(worker_id: int, *, step: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if step == 1:
        builder.button(text="Да, продолжить", callback_data=f"workers:revoke_again:{worker_id}")
    else:
        builder.button(text="ДА, ЗАБРАТЬ ДОСТУП", callback_data=f"workers:revoke_confirm:{worker_id}")
    builder.button(text="Отмена", callback_data=f"workers:open:{worker_id}")
    builder.adjust(1)
    return builder.as_markup()


def workers_cancel_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="workers:menu")
    return builder.as_markup()
