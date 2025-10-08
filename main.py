# main.py — RedditStatusCheckerBot (Reports из Reddit, статус из Reddit Status)
# Работает на aiogram v3. Подходит и для Render Web Service (с health-сервером).

import os
import io
import math
import asyncio
import datetime as dt

import httpx

import matplotlib
matplotlib.use("Agg")  # безопасный бэкенд для хостинга без дисплея
import matplotlib.pyplot as plt

from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types.input_file import BufferedInputFile

import time

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")

# ==== Настройки через переменные окружения ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

USER_AGENT = os.getenv("USER_AGENT", "RedditStatusCheckerBot/1.1 (+https://example.com)")
ENABLE_HTTP = os.getenv("ENABLE_HTTP", "1") == "1"  # для Render Web Service по умолчанию включено
PORT = int(os.getenv("PORT", "10000"))

# ==== Источники данных ====
STATUS_URL = "https://www.redditstatus.com/api/v2/summary.json"  # официальный статус
SEARCH_URL = "https://www.reddit.com/search.json"                # публичный поиск (без OAuth)

# ==== Локализация (RU/EN) ====
LANGS = {
    "ru": {
        "status_ok": "✅ Reddit работает нормально.",
        "status_down": "⚠️ Проблемы на Reddit!",
        "title": "Reports (24ч)",
        "x_label": "Время (UTC)",
        "y_label": "Reports",
        "reports": "Reports (за час)",
        "no_data": "Пока нет данных / No data yet.",
        "web": "Web version",
        "date": "Дата (UTC)",
        "help": (
            "👋 Привет! Я показываю статус Reddit и график 'Reports'.\n\n"
            "Команды:\n"
            "/status — статус + график Reports (24ч)\n"
            "/graph — только график Reports (24ч)"
        ),
    },
    "en": {
        "status_ok": "✅ Reddit is operating normally.",
        "status_down": "⚠️ Reddit seems to be having issues!",
        "title": "Reports (24h)",
        "x_label": "Time (UTC)",
        "y_label": "Reports",
        "reports": "Reports (last hour)",
        "no_data": "No data yet.",
        "web": "Web version",
        "date": "Date (UTC)",
        "help": (
            "👋 Hi! I show Reddit status and a 'Reports' graph.\n\n"
            "Commands:\n"
            "/status — status + Reports graph (24h)\n"
            "/graph — Reports graph (24h) only"
        ),
    },
}

# ==== Инициализация бота ====
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _lang(msg: types.Message) -> str:
    lc = (msg.from_user.language_code or "").lower()
    return "ru" if lc.startswith("ru") else "en"


# ---------- Официальный статус Reddit ----------
async def fetch_status_summary() -> dict:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(STATUS_URL)
        r.raise_for_status()
        return r.json()


# ---------- Reports из Reddit-поиска ----------
def _bucket_by_hour(children: list) -> dict:
    """Группируем посты по часу (UTC)."""
    buckets = {}
    for it in children:
        d = it.get("data", {})
        ts = d.get("created_utc")
        if ts is None:
            continue
        t = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
        buckets[t] = buckets.get(t, 0) + 1
    return buckets


async def fetch_reports_series_24h() -> list[tuple[dt.datetime, int]]:
    """
    Возвращает [(t_hour, count), ...] за последние 24 часа
    по запросу '(reddit down) OR (is reddit down)'.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params = {
        "q": "(reddit down) OR (is reddit down)",
        "sort": "new",
        "t": "day",
        "limit": 250,        # максимум результатов на выдачу
        "restrict_sr": "0",
    }
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers) as c:
            r = await c.get(SEARCH_URL, params=params)
            if r.status_code != 200:
                return []
            data = r.json() or {}
            children = data.get("data", {}).get("children", [])
    except Exception:
        return []

    buckets = _bucket_by_hour(children)
    now = _now_utc().replace(minute=0, second=0, microsecond=0)
    start = now - dt.timedelta(hours=24)

    series = []
    cur = start
    while cur <= now:
        series.append((cur, buckets.get(cur, 0)))
        cur += dt.timedelta(hours=1)
    return series


# ---------- Рендер графика ----------
def plot_reports(points: list[tuple[dt.datetime, int]], lang: str = "en") -> io.BytesIO | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    plt.figure(figsize=(6, 3))
    plt.plot(xs, ys, marker="o")
    plt.title(LANGS[lang]["title"])
    plt.xlabel(LANGS[lang]["x_label"])
    plt.ylabel(LANGS[lang]["y_label"])
    plt.grid(True, alpha=0.3)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


# ---------- Команды ----------
@dp.message(Command(commands=["start", "help"]))
async def start_cmd(msg: types.Message):
    lang = _lang(msg)
    await msg.answer(LANGS[lang]["help"])


@dp.message(Command(commands=["status"]))
async def status_cmd(msg: types.Message):
    lang = _lang(msg)

    # 1) статус Reddit (официальный)
    data = await fetch_status_summary()
    description = data["status"]["description"]
    ok = "Operational" in description
_token_cache = {"access_token": None, "exp": 0}

async def get_reddit_token() -> str | None:
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return None
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["exp"] - 60:
        return _token_cache["access_token"]
    auth = httpx.BasicAuth(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
    data = {"grant_type": "client_credentials"}
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://www.reddit.com/api/v1/access_token",
                         auth=auth, data=data, headers=headers)
        r.raise_for_status()
        j = r.json()
    _token_cache["access_token"] = j["access_token"]
    _token_cache["exp"] = now + j.get("expires_in", 3600)
    return _token_cache["access_token"]

    # 2) серия Reports за 24ч
    async def fetch_reports_series_24h() -> list[tuple[dt.datetime, int]]:
    """
    Пытаемся через официальный OAuth (надёжно). Если ключей нет — падаем
    обратно на публичный search.json как раньше.
    """
    now_ts = int(_now_utc().timestamp())
    hour_ago = now_ts - 24 * 3600

    token = await get_reddit_token()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    items = []
    try:
        if token:
            headers["Authorization"] = f"bearer {token}"
            # cloudsearch-синтаксис: фильтр по времени
            params = {
                "q": f"(reddit down) OR (is reddit down) AND timestamp:{hour_ago}..{now_ts}",
                "syntax": "cloudsearch",
                "sort": "new",
                "limit": 100,
                "type": "link",
            }
            url = "https://oauth.reddit.com/search"
            async with httpx.AsyncClient(timeout=20, headers=headers) as c:
                after = None
                for _ in range(5):  # до 5 страниц
                    pr = dict(params)
                    if after:
                        pr["after"] = after
                    r = await c.get(url, params=pr)
                    if r.status_code != 200:
                        break
                    d = r.json().get("data", {})
                    kids = d.get("children", [])
                    items.extend(kids)
                    after = d.get("after")
                    if not after:
                        break
        else:
            # fallback: публичный search.json (может 429/403)
            params = {
                "q": "(reddit down) OR (is reddit down)",
                "sort": "new",
                "t": "day",
                "limit": 250,
                "restrict_sr": "0",
                "raw_json": 1,
            }
            async with httpx.AsyncClient(timeout=20, headers=headers) as c:
                r = await c.get(SEARCH_URL, params=params)
                if r.status_code == 200:
                    items = (r.json() or {}).get("data", {}).get("children", [])
    except Exception:
        items = []

    # сгруппировать по часам
    buckets = _bucket_by_hour(items)
    now = _now_utc().replace(minute=0, second=0, microsecond=0)
    start = now - dt.timedelta(hours=24)
    series = []
    cur = start
    while cur <= now:
        series.append((cur, buckets.get(cur, 0)))
        cur += dt.timedelta(hours=1)
    return series



@dp.message(Command(commands=["graph"]))
async def graph_cmd(msg: types.Message):
    lang = _lang(msg)
    series = await fetch_reports_series_24h()
    buf = plot_reports(series, lang)
    if not buf:
        await msg.answer(LANGS[lang]["no_data"])
        return
    await msg.answer_photo(BufferedInputFile(buf.getvalue(), filename="reports_24h.png"))


# ---------- Мини HTTP-сервер (для Render Web Service) ----------
async def health(request):
    return web.Response(text="ok")

async def run_http_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"HTTP health server on :{PORT}")


# ---------- Точка входа ----------
async def main():
    if ENABLE_HTTP:
        asyncio.create_task(run_http_server())
    print("Bot polling starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
