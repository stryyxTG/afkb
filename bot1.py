import asyncio
from contextlib import suppress
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from tglol.code_receiver_runner import run_code_receiver_listener
from tglol.config import load_config
from tglol.db import init_db
from tglol.handlers import router
from tglol.paths import ensure_storage


async def main() -> None:
    config = load_config()
    ensure_storage(config)
    init_db(config)
    logging.basicConfig(level=logging.INFO)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=config.bot_parse_mode),
    )
    dp = Dispatcher()
    dp.include_router(router)

    receiver_task = asyncio.create_task(run_code_receiver_listener(bot, config))
    try:
        await dp.start_polling(bot, config=config)
    finally:
        receiver_task.cancel()
        with suppress(asyncio.CancelledError):
            await receiver_task


if __name__ == "__main__":
    asyncio.run(main())
