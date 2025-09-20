import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LoginUrl

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("tg_login_bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_NAME = os.getenv("TELEGRAM_BOT_NAME", "").strip().lstrip("@")
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8080").rstrip("/")

if not TELEGRAM_BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN is not set. Exiting.")
    raise SystemExit(1)
if not TELEGRAM_BOT_NAME:
    logger.critical("TELEGRAM_BOT_NAME is not set. Exiting.")
    raise SystemExit(1)

LOGIN_CALLBACK_URL = f"{SITE_BASE_URL}/telegram/login_callback"

def login_keyboard() -> InlineKeyboardMarkup:
    # Telegram will append user auth params + hash when opening this URL
    btn = InlineKeyboardButton(
        text="Login to ttr.rip",
        login_url=LoginUrl(
            url=LOGIN_CALLBACK_URL,
            bot_username=TELEGRAM_BOT_NAME,
            request_write_access=True,  # matches site widget behavior
        ),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

async def on_start(message: Message):
    text = (
        "Welcome to ttr.rip!\n\n"
        "Tap the button below to log in via Telegram—no phone number entry needed."
    )
    await message.answer(text, reply_markup=login_keyboard())

async def on_login(message: Message):
    text = "Use the button below to log in:"
    await message.answer(text, reply_markup=login_keyboard())

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(on_start, CommandStart())
    dp.message.register(on_login, Command("login"))

    logger.info("Starting Telegram login bot (polling).")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
