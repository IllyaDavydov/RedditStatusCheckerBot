import os
import io
import asyncio
import datetime as dt
import httpx
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types.input_file import BufferedInputFile

BOT_TOKEN = os.getenv("BOT_TOKEN")
STATUS_URL = "https://www.redditstatus.com/api/v2/summary.json"
USER_AGENT = os.getenv("USER_AGENT", "RedditStatusCheckerBot/1.0 (+https://example.com)")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

LANGS = {
    "ru": {
        "status_ok": "✅ Reddit работает нормально.",
        "status_down": "⚠️ Проблемы на Reddit!",
        "title": "График сбоев Reddit",
        "x_label": "Время",
        "y_label": "Количество инцидентов",
        "reports": "Reports (за час)",
        "incidents": "Активных инцидентов",
        "no_data": "Пока нет данных / No data yet.",
        "web": "Web version",
        "date": "Дата (UTC)",
    },
    "en": {
        "status_ok": "✅ Reddit is operating normally.",
        "status_down": "⚠️ Reddit seems to be having issues!",
        "title": "Reddit Outage Graph",
        "x_label": "Time",
        "y_label": "Incident Count",
        "reports": "Reports (last hour)",
        "incidents": "Active incidents",
        "no_data": "No data yet.",
        "web": "Web version",
        "date": "Date (UTC)",
    },
}

status_cache = {"last_status": None, "history": []}


async def fetch_status():
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(STATUS_URL)
        r.raise_for_status()
        return r.json()


async def fetch_reports_last_hour():
    """
    Простейшая метрика 'Reports': кол-во свежих постов по запросу
    'reddit down' ИЛИ 'is reddit down' за последний час.
    Без OAuth, через публичный поиск reddit.com (может ограничиваться rate-limit).
    Если не получилось — вернём None.
    """
    url = "https://www.reddit.com/search.json"
    params = {
        "q": "(reddit down) OR (is reddit down)",
        "sort": "new",
        "t": "hour",
        "limit": 100,
        "restrict_sr": "0",
        "include_over_18": "on",
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                return None
            data = r.json()
            children = (data or {}).get("data", {}).get("children", [])
            return len(children)
    except Exception:
        return None


def _now_utc():
    return dt.datetime.now(dt.timezone.utc)


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


def _lang(msg: types.Message) -> str:
    lc = (msg.from_user.language_code or "").lower()
    return "ru" if lc.startswith("ru") else "en"


@dp.message(Command(commands=["start", "help"]))
async def start(msg: types.Message):
    await msg.answer(
        "👋 Привет! Я показываю статус Reddit и графики сбоев.\n\n"
        "Команды:\n"
        "/status — текущее состояние + график\n"
        "/graph — график за последние 24 часа\n\n"
        "👋 Hi! I show Reddit's current status and outage graphs.\n\n"
        "Commands:\n"
        "/status — current status + graph\n"
        "/graph — outage graph (24h)"
    )


@dp.message(Command(commands=["status"]))
async def status_cmd(msg: types.Message):
    lang = _lang(msg)

    # 1) тянем статус
    data = await fetch_status()
    description = data["status"]["description"]
    incidents = data.get("incidents", [])
    open_count = len([i for i in incidents if i["status"] != "resolved"])

    # 2) обновляем историю (5-минутная сетка)
    now = _now_utc()
    status_cache["history"].append((now, open_count))
    status_cache["history"] = status_cache["history"][-288:]  # ~24h при шаге 5 минут

    # 3) готовим график (если нет данных — добавим стартовую точку)
    if not status_cache["history"]:
        status_cache["history"].append((now, open_count))
    buf = plot_history(lang)
    if not buf:
        await msg.answer(LANGS[lang]["no_data"])
        return

    # 4) считаем "Reports" (упоминания за час) — может быть None
    reports = await fetch_reports_last_hour()
    rep_str = str(reports) if isinstance(reports, int) else "—"

    # 5) текст подписи (caption)
    ok = "Operational" in description
    header = LANGS[lang]["status_ok"] if ok else LANGS[lang]["status_down"]
    caption = (
        f"{header}\n\n"
        f"🌐 {description}\n"
        f"📊 {LANGS[lang]['reports']}: {rep_str}\n"
        f"🧰 {LANGS[lang]['incidents']}: {open_count}\n"
        f"🕒 {LANGS[lang]['date']}: {now.strftime('%Y-%m-%d %H:%M')}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANGS[lang]["web"], url="https://www.redditstatus.com/")]
    ])

    await msg.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="reddit_graph.png"),
        caption=caption,
        reply_markup=kb
    )


@dp.message(Command(commands=["graph"]))
async def graph_cmd(msg: types.Message):
    lang = _lang(msg)

    # если данных ещё нет — снимем текущее состояние и добавим точку
    if not status_cache["history"]:
        data = await fetch_status()
        incidents = data.get("incidents", [])
        count = len([i for i in incidents if i["status"] != "resolved"])
        status_cache["history"].append((_now_utc(), count))

    buf = plot_history(lang)
    if not buf:
        await msg.answer(LANGS[lang]["no_data"])
        return

    await msg.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="reddit_graph.png")
    )


async def auto_check():
    """Фоновая проверка раз в 5 минут (для истории и будущих алертов)."""
    alert_chat = os.getenv("ALERT_CHAT_ID")  # можно задать chat_id или @channel
    while True:
        try:
            data = await fetch_status()
            description = data["status"]["description"]
            incidents = data.get("incidents", [])
            count = len([i for i in incidents if i["status"] != "resolved"])
            now = _now_utc()
            status_cache["history"].append((now, count))
            status_cache["history"] = status_cache["history"][-288:]
            last = status_cache["last_status"]
            if alert_chat and last is not None and last != description:
                msg = "⚠️ Reddit DOWN!" if "Operational" not in description else "✅ Reddit is back online!"
                try:
                    await bot.send_message(chat_id=alert_chat, text=msg)
                except Exception:
                    pass
            status_cache["last_status"] = description
        except Exception as e:
            print("auto_check error:", e)
        await asyncio.sleep(300)


async def main():
    asyncio.create_task(auto_check())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
