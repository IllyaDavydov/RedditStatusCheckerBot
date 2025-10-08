import os
import io
import asyncio
import datetime as dt
import httpx
import matplotlib.pyplot as plt
from aiogram.filters import Command
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN")
STATUS_URL = "https://www.redditstatus.com/api/v2/summary.json"

from aiogram.client.default import DefaultBotProperties

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher()

LANGS = {
    "ru": {
        "status_ok": "✅ Reddit работает нормально.",
        "status_down": "⚠️ Проблемы на Reddit!",
        "title": "График сбоев Reddit",
        "x_label": "Время",
        "y_label": "Количество инцидентов",
    },
    "en": {
        "status_ok": "✅ Reddit is operating normally.",
        "status_down": "⚠️ Reddit seems to be having issues!",
        "title": "Reddit Outage Graph",
        "x_label": "Time",
        "y_label": "Incident Count",
    },
}

status_cache = {"last_status": None, "history": []}


async def fetch_status():
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(STATUS_URL)
        r.raise_for_status()
        return r.json()


def plot_history(lang="en"):
    if not status_cache["history"]:
        return None
    times = [x[0] for x in status_cache["history"]]
    values = [x[1] for x in status_cache["history"]]
    plt.figure(figsize=(6, 3))
    plt.plot(times, values, marker="o")
    plt.title(LANGS[lang]["title"])
    plt.xlabel(LANGS[lang]["x_label"])
    plt.ylabel(LANGS[lang]["y_label"])
    plt.grid(True, alpha=0.3)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


@dp.message(Command(commands=["start", "help"]))
async def start(msg: types.Message):
    lang = "ru" if msg.from_user.language_code.startswith("ru") else "en"
    await msg.answer(
        "👋 Привет! Я показываю статус Reddit и графики сбоев.\n\n"
        "Команды:\n"
        "/status — текущее состояние\n"
        "/graph — график за последние 24 часа\n\n"
        "👋 Hi! I show Reddit's current status and outage graphs.\n\n"
        "Commands:\n"
        "/status — show current status\n"
        "/graph — outage graph (24h)"
    )


@dp.message(Command(commands=["status"]))
async def status_cmd(msg: types.Message):
    lang = "ru" if msg.from_user.language_code.startswith("ru") else "en"
    data = await fetch_status()
    description = data["status"]["description"]
    incidents = data.get("incidents", [])
    count = len([i for i in incidents if i["status"] != "resolved"])

    now = dt.datetime.utcnow()
    status_cache["history"].append((now, count))
    status_cache["history"] = status_cache["history"][-288:]  # 24h with 5min step

    if "Operational" in description:
        text = LANGS[lang]["status_ok"]
    else:
        text = LANGS[lang]["status_down"]

    text += f"\n\n🌐 {description}\nАктивных инцидентов: {count}"
    await msg.answer(text)


@dp.message(Command(commands=["graph"]))
async def graph_cmd(msg: types.Message):
    lang = "ru" if msg.from_user.language_code.startswith("ru") else "en"
    buf = plot_history(lang)
    if not buf:
        await msg.answer("Пока нет данных / No data yet.")
        return
    await msg.answer_photo(types.input_file.InputFile(buf, filename="reddit_graph.png"))


async def auto_check():
    while True:
        try:
            data = await fetch_status()
            description = data["status"]["description"]
            incidents = data.get("incidents", [])
            count = len([i for i in incidents if i["status"] != "resolved"])
            now = dt.datetime.utcnow()
            status_cache["history"].append((now, count))
            status_cache["history"] = status_cache["history"][-288:]
            last = status_cache["last_status"]

            if last != description:
                status_cache["last_status"] = description
                msg = "⚠️ Reddit DOWN!" if "Operational" not in description else "✅ Reddit is back online!"
                await bot.send_message(chat_id="@RedditStatusCheckerChannel", text=msg)
        except Exception as e:
            print("Error:", e)
        await asyncio.sleep(300)  # check every 5 minutes


async def main():
    asyncio.create_task(auto_check())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
