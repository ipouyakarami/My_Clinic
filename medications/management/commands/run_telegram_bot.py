"""
Telegram bot management command for MyClinic.

Reminder-only bot: patients receive medication reminders in Telegram
and manage their intake (taken/skipped) exclusively through the web app.

Commands:
  /start      — Generate activation code (or show "already connected")
  /help       — Show available commands
  /menu       — Show connection status and available commands
  /disconnect — Unlink Telegram from patient account

The bot token must be set in the TELEGRAM_BOT_TOKEN environment variable.

Usage:
    python manage.py run_telegram_bot
"""

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from telegram import ReplyKeyboardRemove, Update
from telegram.error import Conflict
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)


def get_bot_token():
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) or __import__("os").environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise CommandError(
            "Telegram bot token is not set. Add TELEGRAM_BOT_TOKEN to your "
            ".env file (see .env.example). Get a token from @BotFather."
        )
    return token


def get_bot_username():
    username = getattr(settings, "TELEGRAM_BOT_USERNAME", None) or __import__("os").environ.get("TELEGRAM_BOT_USERNAME")
    if username:
        return username.lstrip("@")
    return "myclinicmedicationreminderbot"


def get_linked_patient(chat_id):
    """Return the Patient linked to this chat, or None."""
    from accounts.models import Patient

    try:
        return Patient.objects.select_related("user").get(telegram_chat_id=chat_id)
    except Patient.DoesNotExist:
        return None


async_get_linked_patient = sync_to_async(get_linked_patient)


def _get_activation_code(chat_id):
    from medications.models import TelegramActivationCode
    return TelegramActivationCode.get_valid_for_chat(chat_id)


def _generate_activation_code(chat_id, username):
    from medications.models import TelegramActivationCode
    return TelegramActivationCode.generate_for_chat(chat_id, username)


def _save_patient(patient):
    patient.save(update_fields=["telegram_chat_id", "telegram_username"])


async_get_activation_code = sync_to_async(_get_activation_code)
async_generate_activation_code = sync_to_async(_generate_activation_code)
async_save_patient = sync_to_async(_save_patient)


async def _error_handler(update, context):
    """Log instead of crashing so the polling loop can recover."""
    exc = context.error
    if isinstance(exc, Conflict):
        logger.warning(
            "Long-polling conflict (another instance may be running with the "
            "same token) — keeping the loop alive and retrying…"
        )
        return
    logger.error("Telegram bot error: %s", exc)


class Command(BaseCommand):
    help = "Run the MyClinic Telegram bot (long-polling, reminder-only)"

    def handle(self, *args, **options):
        token = get_bot_token()
        bot_username = get_bot_username()

        application = ApplicationBuilder().token(token).build()
        application.add_error_handler(_error_handler)

        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("menu", self.menu_command))
        application.add_handler(CommandHandler("disconnect", self.disconnect))

        logger.info("Starting MyClinic Telegram bot (username: @%s)", bot_username)
        self.stdout.write(
            self.style.SUCCESS(
                f"Starting MyClinic Telegram bot (username: @{bot_username})..."
            )
        )

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            application.run_polling()
        except Exception as exc:
            logger.error("Telegram bot error: %s", exc)
            raise

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        chat_id = update.effective_chat.id
        chat = update.effective_chat
        username = chat.username if chat else None

        patient = await async_get_linked_patient(chat_id)
        if patient:
            full_name = await sync_to_async(patient.user.get_full_name)()
            await update.message.reply_text(
                f"✅ Your Telegram account is already connected to MyClinic "
                f"({full_name}).\n\n"
                f"You will receive medication reminders here. To manage your "
                f"medications, visit the web app.",
            )
            return

        existing_code = await async_get_activation_code(chat_id)
        if existing_code:
            code = existing_code.code
            expires_in = int((existing_code.expires_at - timezone.now()).total_seconds() // 60)
        else:
            activation_code = await async_generate_activation_code(chat_id, username)
            code = activation_code.code
            expires_in = 10

        await update.message.reply_text(
            f"👋 Welcome to MyClinic Bot!\n\n"
            f"To connect your Telegram account to MyClinic, enter this code "
            f"on the site:\n\n"
            f"   <b>{code}</b>\n\n"
            f"The code expires in {expires_in} minutes.\n\n"
            f"Go to: {settings.SITE_URL}/telegram/connect/",
            reply_markup=ReplyKeyboardRemove(),
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        text = (
            "🤖 MyClinic Telegram Bot\n\n"
            "This bot sends you medication reminders.\n\n"
            "Commands:\n"
            "/start — Connect your Telegram to MyClinic\n"
            "/menu — Show connection status\n"
            "/help — Show this help message\n"
            "/disconnect — Unlink your Telegram account\n\n"
            "To mark medications as taken or skipped, use the web app."
        )

        patient = await async_get_linked_patient(update.effective_chat.id)
        if patient:
            text += "\n\n✅ You are connected to your MyClinic account."

        await update.message.reply_text(text)

    async def menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /menu command — show connection status."""
        patient = await async_get_linked_patient(update.effective_chat.id)
        if not patient:
            await update.message.reply_text(
                "🔒 Your Telegram account is not connected to MyClinic.\n\n"
                "Send /start to get an activation code and connect your account.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        full_name = await sync_to_async(patient.user.get_full_name)()
        await update.message.reply_text(
            f"📋 MyClinic Bot\n\n"
            f"✅ Connected as: {full_name}\n"
            f"📧 Email: {patient.user.email}\n\n"
            f"You will receive medication reminders here.\n"
            f"To manage medications, visit the web app.\n\n"
            f"Commands:\n"
            f"/help — Show help\n"
            f"/disconnect — Unlink your Telegram account",
        )

    async def disconnect(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /disconnect command."""
        chat_id = update.effective_chat.id

        patient = await async_get_linked_patient(chat_id)
        if patient:
            patient.telegram_chat_id = None
            patient.telegram_username = None
            await async_save_patient(patient)
            await update.message.reply_text(
                "❌ Your Telegram account has been disconnected from MyClinic.\n\n"
                "You will no longer receive medication reminders here.\n"
                "You can reconnect anytime by sending /start.",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await update.message.reply_text(
                "⚠️ Your Telegram account is not currently linked to any "
                "MyClinic account.\n\nTo connect, send /start first.",
            )
