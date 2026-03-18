"""
Tastytrade-style 0DTE SPX Credit Spread Scanner — Telegram Bot
Railway cloud deployment version. All config from environment variables.
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut, RetryAfter

from market_data import MarketDataEngine
from ticket_formatter import (
    format_trade_ticket, format_vix_report, format_spx_report, format_calendar_report,
)
from ai_engine import AIEngine

# ─── Configuration from environment ─────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

# ─── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SPXBot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ─── Globals ─────────────────────────────────────────────────────────────
market = MarketDataEngine()
ai = AIEngine()
_last_snapshot = None
_last_daily_scan_date = None


def get_snapshot():
    global _last_snapshot
    _last_snapshot = market.get_full_snapshot()
    return _last_snapshot


# ─── Commands ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "🎯 0DTE SPX Credit Spread Scanner\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome! I scan the SPX options chain daily for optimal 0DTE credit spread setups "
        "and format them for entry in Fidelity.\n\n"
        "Commands:\n"
        "/scan — Fresh 0DTE scan & trade ticket\n"
        "/ticket — Today's trade ticket\n"
        "/vix — VIX & premium selling conditions\n"
        "/spx — SPX price & expected move\n"
        "/calendar — Today's economic events\n"
        "/help — Full command list & strategy guide\n\n"
        "💬 Ask me anything about options in plain English!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Bot started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}\n"
        "☁️ Running on Railway (24/7)"
    )
    await update.message.reply_text(welcome)


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning SPX options chain for 0DTE setups...\nThis may take 10-15 seconds.")
    try:
        snapshot = get_snapshot()
        ticket = format_trade_ticket(snapshot)
        await _send_long_message(update, ticket)
    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Scan failed: {str(e)[:200]}\nTry again in a moment.")


async def cmd_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Generating today's trade ticket...")
    try:
        snapshot = get_snapshot()
        ticket = format_trade_ticket(snapshot)
        await _send_long_message(update, ticket)
    except Exception as e:
        logger.error(f"Ticket error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Failed: {str(e)[:200]}")


async def cmd_vix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = format_vix_report(market.get_vix())
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"❌ VIX fetch failed: {str(e)[:200]}")


async def cmd_spx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = format_spx_report(market.get_spx_price(), market.get_options_data())
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"❌ SPX fetch failed: {str(e)[:200]}")


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = format_calendar_report(market.get_economic_calendar())
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar fetch failed: {str(e)[:200]}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🎯 0DTE SPX Credit Spread Scanner — Help\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "COMMANDS:\n"
        "/scan — Fresh 0DTE scan with full trade ticket\n"
        "/ticket — Today's trade ticket\n"
        "/vix — VIX level & premium selling assessment\n"
        "/spx — SPX price & expected move\n"
        "/calendar — Economic events & catalyst check\n"
        "/help — This help message\n\n"
        "STRATEGY:\n"
        "1. Put Credit Spread — Sell put at 0.10-0.15 delta, buy 5-10 pts below\n"
        "2. Call Credit Spread — Sell call at 0.10-0.15 delta, buy 5-10 pts above\n"
        "3. Iron Condor — Both sides when VIX > 16, no major catalysts\n\n"
        "RISK MANAGEMENT:\n"
        "• Entry: 9:45-10:30 AM ET\n"
        "• Stop: Close at 2x credit\n"
        "• Target: 50% profit or expire worthless\n"
        "• Size: 1-2% of account\n\n"
        "Each ticket includes Fidelity multi-leg order instructions.\n"
        "Ask me anything about options in plain English!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "☁️ Running 24/7 on Railway\n"
        "Disclaimer: Educational only. NOT financial advice."
    )
    await update.message.reply_text(help_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_msg = update.message.text.strip()
    if not user_msg:
        return
    user_id = update.message.from_user.id
    try:
        market_context = ""
        try:
            global _last_snapshot
            if _last_snapshot is None:
                _last_snapshot = get_snapshot()
            market_context = ai.get_market_context_string(_last_snapshot)
        except Exception:
            pass
        response = await ai.chat(user_id, user_msg, market_context)
        await _send_long_message(update, response)
    except Exception as e:
        logger.error(f"Message handler error: {e}", exc_info=True)
        await update.message.reply_text("I had trouble processing that. Try /scan for a trade ticket.")


# ─── Utilities ───────────────────────────────────────────────────────────

async def _send_long_message(update: Update, text: str, max_len: int = 4000):
    if len(text) <= max_len:
        await update.message.reply_text(text)
        return
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > max_len:
            if chunk:
                await update.message.reply_text(chunk)
            chunk = line
        else:
            chunk = chunk + "\n" + line if chunk else line
    if chunk:
        await update.message.reply_text(chunk)


async def send_direct(app: Application, text: str):
    max_len = 4000
    if len(text) <= max_len:
        await app.bot.send_message(chat_id=CHAT_ID, text=text)
        return
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > max_len:
            if chunk:
                await app.bot.send_message(chat_id=CHAT_ID, text=chunk)
                await asyncio.sleep(0.5)
            chunk = line
        else:
            chunk = chunk + "\n" + line if chunk else line
    if chunk:
        await app.bot.send_message(chat_id=CHAT_ID, text=chunk)


# ─── Daily Scheduler ────────────────────────────────────────────────────

async def daily_scheduler(app: Application):
    global _last_daily_scan_date
    logger.info("Daily scheduler started.")
    await asyncio.sleep(5)
    while True:
        try:
            now_et = datetime.now()
            today_str = now_et.strftime("%Y-%m-%d")
            weekday = now_et.weekday()
            is_trading_day = weekday < 5
            market_open = now_et.hour >= 9 and (now_et.hour > 9 or now_et.minute >= 30)
            if is_trading_day and market_open and _last_daily_scan_date != today_str:
                logger.info(f"Sending daily scan for {today_str}...")
                try:
                    snapshot = get_snapshot()
                    ticket = format_trade_ticket(snapshot)
                    header = f"🌅 GOOD MORNING — Daily 0DTE Scan for {now_et.strftime('%A, %B %d')}\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    await send_direct(app, header + ticket)
                    _last_daily_scan_date = today_str
                    logger.info("Daily scan delivered.")
                except Exception as e:
                    logger.error(f"Daily scan failed: {e}", exc_info=True)
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
            await asyncio.sleep(60)


# ─── Startup ─────────────────────────────────────────────────────────────

async def post_init(app: Application):
    logger.info("Bot initialized — running startup sequence")
    commands = [
        BotCommand("scan", "Run fresh 0DTE scan & trade ticket"),
        BotCommand("ticket", "Get today's trade ticket"),
        BotCommand("vix", "VIX & premium selling conditions"),
        BotCommand("spx", "SPX price & expected move"),
        BotCommand("calendar", "Today's economic events"),
        BotCommand("help", "Command list & strategy guide"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Failed to set commands: {e}")

    startup_msg = (
        "🟢 ROLAND BOT — ONLINE (Railway Cloud)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}\n"
        "Hosting: Railway (24/7 persistent)\n"
        "Status: Polling Telegram ✅\n"
        "Daily scheduler: Active ✅\n\n"
        "Running initial market scan..."
    )
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=startup_msg)
    except Exception as e:
        logger.error(f"Startup message failed: {e}")

    try:
        logger.info("Running startup scan...")
        snapshot = get_snapshot()
        ticket = format_trade_ticket(snapshot)
        await send_direct(app, ticket)
        logger.info("Startup scan delivered.")
    except Exception as e:
        logger.error(f"Startup scan failed: {e}", exc_info=True)
        try:
            await app.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Startup scan issue: {str(e)[:200]}\nUse /scan to retry.")
        except Exception:
            pass

    asyncio.create_task(daily_scheduler(app))
    logger.info("Daily scheduler launched.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning(f"Network/timeout error (auto-retry): {err}")
    elif isinstance(err, RetryAfter):
        logger.warning(f"Rate limited — retry after {err.retry_after}s")
    else:
        logger.error(f"Unhandled error: {err}", exc_info=err)


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("Starting Roland 0DTE SPX Bot on Railway")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("ticket", cmd_ticket))
    app.add_handler(CommandHandler("vix", cmd_vix))
    app.add_handler(CommandHandler("spx", cmd_spx))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot polling started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, timeout=30)


if __name__ == "__main__":
    main()
