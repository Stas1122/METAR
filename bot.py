import asyncio
import logging
import os
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from monitor import WeatherMonitor

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WAITING_STATION = 1
WAITING_THRESHOLD = 2

monitor = WeatherMonitor()


def main_keyboard():
    keyboard = [
        [KeyboardButton("➕ Додати станцію"), KeyboardButton("📋 Мої станції")],
        [KeyboardButton("🌡 Температура зараз"), KeyboardButton("🗑 Видалити")],
        [KeyboardButton("📈 Статус"), KeyboardButton("❓ Допомога")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Weather Monitor*\n\n"
        "Відстежую температуру на аеропортах через METAR.\n"
        "Сповіщення приходить одразу як температура змінилась.\n\n"
        "Використовуй кнопки внизу або команди:\n"
        "/add · /list · /remove · /temp"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Довідка*\n\n"
        "➕ *Додати станцію* — вводиш код аеропорту (KSEA, CYYZ...)\n"
        "📋 *Мої станції* — список активних станцій\n"
        "🌡 *Температура зараз* — поточні дані по всіх станціях\n"
        "🗑 *Видалити* — прибрати станцію\n\n"
        "🔔 *Сповіщення:*\n"
        "▸ Температура змінилась — одразу\n"
        "▸ Перевірка кожну хвилину\n\n"
        "*Популярні станції:*\n"
        "`KSEA` — Сіетл\n"
        "`KJFK` — Нью-Йорк\n"
        "`KORD` — Чикаго\n"
        "`KLAX` — Лос-Анджелес\n"
        "`CYYZ` — Торонто\n"
        "`KBOS` — Бостон\n"
        "`KDFW` — Даллас\n"
        "`KPHX` — Фенікс"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ Введи код аеропорту (ICAO):\n\n"
        "Приклади: `KSEA`, `CYYZ`, `KJFK`\n\n"
        "або /cancel для скасування",
        parse_mode="Markdown"
    )
    return WAITING_STATION


async def receive_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    station = update.message.text.strip().upper()
    chat_id = str(update.effective_chat.id)

    if len(station) < 3 or len(station) > 5 or not station.isalpha():
        await update.message.reply_text(
            "❌ Невірний формат. Код має бути 3-4 літери (наприклад `KSEA`).\nСпробуй ще раз:",
            parse_mode="Markdown"
        )
        return WAITING_STATION

    # Перевіряємо чи станція існує
    await update.message.reply_text(f"⏳ Перевіряю станцію {station}...")
    temp_data = await monitor.fetch_metar(station)

    if temp_data is None:
        await update.message.reply_text(
            f"❌ Станція `{station}` не знайдена або немає даних.\n"
            f"Перевір код і спробуй ще раз:",
            parse_mode="Markdown"
        )
        return WAITING_STATION

    result = monitor.add_station(chat_id, station)

    if result == "exists":
        await update.message.reply_text(
            f"ℹ️ Станція `{station}` вже відстежується.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
    else:
        temp_c = temp_data["temp_c"]
        temp_f = temp_data["temp_f"]
        await update.message.reply_text(
            f"✅ Станцію додано!\n\n"
            f"✈️ *{station}*\n"
            f"🌡 Зараз: *{temp_f:.1f}°F* ({temp_c:.1f}°C)\n"
            f"🟢 Моніторинг активний\n\n"
            f"Сповіщення при кожній зміні температури.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        logger.info(f"Added station {station} for chat {chat_id}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    stations = monitor.get_stations(chat_id)

    if not stations:
        await update.message.reply_text(
            "📋 Список порожній.\n\nДодай станцію через ➕",
            reply_markup=main_keyboard()
        )
        return

    lines = ["📋 *Активні станції:*\n"]
    for s in stations:
        code = s["code"]
        last_temp = s.get("last_temp_f")
        if last_temp:
            last_c = s.get("last_temp_c", 0)
            lines.append(f"✈️ `{code}` — {last_temp:.1f}°F ({last_c:.1f}°C)")
        else:
            lines.append(f"✈️ `{code}` — очікую дані...")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_keyboard())


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    stations = monitor.get_stations(chat_id)

    if not stations:
        await update.message.reply_text("📋 Список порожній.", reply_markup=main_keyboard())
        return

    keyboard = []
    for s in stations:
        code = s["code"]
        keyboard.append([InlineKeyboardButton(f"🗑 {code}", callback_data=f"remove:{code}")])

    await update.message.reply_text(
        "Оберіть станцію для видалення:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def temp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    stations = monitor.get_stations(chat_id)

    if not stations:
        await update.message.reply_text(
            "📋 Список порожній. Додай станцію через ➕",
            reply_markup=main_keyboard()
        )
        return

    msg = await update.message.reply_text("⏳ Отримую дані...")
    lines = ["🌡 *Поточна температура:*\n"]

    for s in stations:
        code = s["code"]
        data = await monitor.fetch_metar(code)
        if data:
            temp_f = data["temp_f"]
            temp_c = data["temp_c"]
            time_str = data.get("time", "")
            lines.append(f"✈️ *{code}*: {temp_f:.1f}°F ({temp_c:.1f}°C)  🕐 {time_str}")
        else:
            lines.append(f"✈️ *{code}*: ❌ немає даних")

    try:
        await msg.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = query.data

    if data.startswith("remove:"):
        code = data.split(":", 1)[1]
        monitor.remove_station(chat_id, code)
        await query.edit_message_text(f"✅ Станцію `{code}` видалено.", parse_mode="Markdown")


async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📋 Мої станції":
        await list_command(update, context)
    elif text == "🌡 Температура зараз":
        await temp_command(update, context)
    elif text == "🗑 Видалити":
        await remove_command(update, context)
    elif text == "📈 Статус":
        await status_command(update, context)
    elif text == "❓ Допомога":
        await help_command(update, context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    stations = monitor.get_stations(chat_id)
    total = monitor.get_total_stations()

    await update.message.reply_text(
        f"📈 *Статус*\n\n"
        f"🟢 Бот активний\n"
        f"✈️ Твоїх станцій: *{len(stations)}*\n"
        f"🌐 Всього станцій: *{total}*\n"
        f"⏱ Перевірка: кожну хвилину\n"
        f"📡 Джерело: METAR (aviationweather.gov)",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def send_temp_notification(bot, chat_id: str, station: str, old_f: float, new_f: float,
                                  old_c: float, new_c: float, time_str: str):
    diff_f = new_f - old_f
    diff_c = new_c - old_c
    arrow = "🔺" if diff_f > 0 else "🔻"
    sign = "+" if diff_f > 0 else ""

    text = (
        f"🌡 *{station} — зміна температури!*\n\n"
        f"{arrow} {old_f:.1f}°F → *{new_f:.1f}°F*  ({sign}{diff_f:.1f}°F)\n"
        f"   {old_c:.1f}°C → *{new_c:.1f}°C*  ({sign}{diff_c:.1f}°C)\n"
        f"🕐 {time_str}"
    )

    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send notification to {chat_id}: {e}")


async def run_monitor_loop(app):
    logger.info("Weather monitor loop started")
    while True:
        try:
            changes = await monitor.check_temperature_changes()
            for chat_id, station, old_f, new_f, old_c, new_c, time_str in changes:
                await send_temp_notification(
                    app.bot, chat_id, station,
                    old_f, new_f, old_c, new_c, time_str
                )
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        await asyncio.sleep(60)  # кожну хвилину


async def run_web_server():
    """Простий веб-сервер щоб Render не таймаутив."""
    from aiohttp import web
    async def health(request):
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_command),
            MessageHandler(filters.Regex("^➕ Додати станцію$"), add_command),
        ],
        states={
            WAITING_STATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_station)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("temp", temp_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_keyboard_buttons
    ))

    async def post_init(application):
        asyncio.create_task(run_monitor_loop(application))
        asyncio.create_task(run_web_server())

    app.post_init = post_init
    logger.info("Weather bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
