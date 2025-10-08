# main.py — RedditStatusCheckerBot
# График "Reports (24h)" по фразам "reddit down / is reddit down" и их RU-аналогам
# Источники: Reddit OAuth Search (надежно) + fallback на публичный search.json
# Статус: официальный Reddit Status. Aiogram v3. Render-friendly.

import os
import io
import time
import asyncio
import datetime as dt
import math

import httpx

import matplotlib
matplotlib.use("Agg")  # для хостинга без дисплея
import matplotlib.pyplot as plt

from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types.input_file import BufferedInputFile


# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# Reddit OAuth (создай app: https://www.reddit.com/prefs/apps)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")

USER_AGENT = os.getenv("USER_AGENT", "RedditStatusCheckerBot/1.3 (+https://example.com)")

# Для Render Web Service
ENABLE_HTTP = os.getenv("ENABLE_HTTP", "1") == "1"  # 1 для веб-сервиса; 0 для воркера
PORT = int(os.getenv("PORT", "10000"))

# ========= URLs =========
STATUS_URL = "https://www.redditstatus.com/api/v2/summary.json"
OAUTH_SEARCH_URL = "https://oauth.reddit.com/search"
PUBLIC_SEARCH_URL = "https://www.reddit.com/search.json"

# ========= L10N =========
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

# ========= BOT =========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ========= Helpers =========
def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def _lang(msg: types.Message) -> str:
    lc = (msg.from_user.language_code or "").lower()
    return "ru" if lc.startswith("ru") else "en"


# ========= Reddit Status =========
async def fetch_status_summary() -> dict:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(STATUS_URL)
        r.raise_for_status()
        return r.json()


# ========= OAuth token cache =========
_token_cache = {"access_token": None, "exp": 0.0}

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
    _token_cache["exp"] = now + float(j.get("expires_in", 3600))
    return _token_cache["access_token"]


# ========= Reports series (24h) =========
def _bucket_by_hour(children: list) -> dict:
    """Группируем посты/комменты по часам UTC; ожидаем поле data.created_utc."""
    buckets = {}
    for it in children:
        d = it.get("data", {})
        ts = d.get("created_utc")
        if ts is None:
            continue
        t = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
        buckets[t] = buckets.get(t, 0) + 1
    return buckets

async def _fetch_public_search_last_24h(phrases: list[str]) -> list:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params = {
        "q": "(" + " OR ".join([f'"{p}"' for p in phrases]) + ")",
        "sort": "new",
        "t": "day",
        "limit": 250,
        "restrict_sr": "0",
        "raw_json": 1,
    }
    async with httpx.AsyncClient(timeout=20, headers=headers) as c:
        r = await c.get(PUBLIC_SEARCH_URL, params=params)
        if r.status_code != 200:
            return []
        return (r.json() or {}).get("data", {}).get("children", [])

async def _oauth_search(types: list[str], phrases: list[str]) -> list:
    """OAuth search: типы: 'link'|'self'|'comment'. Пагинация до ~6 страниц на тип."""
    token = await get_reddit_token()
    if not token:
        return []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": f"bearer {token}",
    }
    now_ts = int(_now_utc().replace(minute=0, second=0, microsecond=0).timestamp())
    start_ts = now_ts - 24 * 3600
    items = []
    async with httpx.AsyncClient(timeout=20, headers=headers) as c:
        for t in types:
            after = None
            for _ in range(6):
                query = "(" + " OR ".join([f'"{p}"' for p in phrases]) + f") AND timestamp:{start_ts}..{now_ts}"
                params = {
                    "q": query,
                    "syntax": "cloudsearch",
                    "sort": "new",
                    "limit": 100,
                    "type": t,
                    "raw_json": 1,
                }
                if after:
                    params["after"] = after
                r = await c.get(OAUTH_SEARCH_URL, params=params)
                if r.status_code != 200:
                    break
                d = r.json().get("data", {})
                kids = d.get("children", []) or []
                items.extend(kids)
                after = d.get("after")
                if not after:
                    break
    return items

async def fetch_reports_series_24h() -> list[tuple[dt.datetime, int]]:
    """
    Считаем Reports по постам И комментариям на Reddit за 24 часа.
    Ключевые фразы EN/RU. Сначала OAuth, затем fallback на публичный.
    """
    phrases = [
        "reddit down", "is reddit down", "reddit not working", "reddit outage",
        "реддит не работает", "упал реддит", "reddit лежит"
    ]

    items: list = []
    try:
        # посты (link/self) + комментарии
        items = await _oauth_search(["link", "self", "comment"], phrases)
    except Exception:
        items = []

    if not items:
        try:
            items = await _fetch_public_search_last_24h(phrases)
        except Exception:
            items = []

    buckets = _bucket_by_hour(items)
    now_dt = _now_utc().replace(minute=0, second=0, microsecond=0)
    start = now_dt - dt.timedelta(hours=24)
    series = []
    cur = start
    while cur <= now_dt:
        series.append((cur, buckets.get(cur, 0)))
        cur += dt.timedelta(hours=1)
    return series


# ========= Plot =========
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


# ========= Handlers =========
@dp.message(Command(commands=["start", "help"]))
async def start_cmd(msg: types.Message):
    await msg.answer(LANGS[_lang(msg)]["help"])

@dp.message(Command(commands=["status"]))
async def status_cmd(msg: types.Message):
    lang = _lang(msg)

    # статус Reddit (официальный)
    try:
        data = await fetch_status_summary()
        description = data["status"]["description"]
        ok = "Operational" in description
    except Exception:
        description = "Unknown"
        ok = True

    # график Reports 24h
    series = await fetch_reports_series_24h()
    buf = plot_reports(series, lang)
    if not buf:
        await msg.answer(LANGS[lang]["no_data"])
        return

    reports_last_hour = series[-1][1] if series else 0
    now = _now_utc()

    caption = (
        f"{LANGS[lang]['status_ok'] if ok else LANGS[lang]['status_down']}\n\n"
        f"🌐 {description}\n"
        f"📊 {LANGS[lang]['reports']}: {reports_last_hour}\n"
        f"🕒 {LANGS[lang]['date']}: {now.strftime('%Y-%m-%d %H:%M')}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Web version", url="https://downdetector.com/status/reddit/")]]
    )
    await msg.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="reports_24h.png"),
        caption=caption,
        reply_markup=kb
    )

@dp.message(Command(commands=["graph"]))
async def graph_cmd(msg: types.Message):
    lang = _lang(msg)
    series = await fetch_reports_series_24h()
    buf = plot_reports(series, lang)
    if not buf:
        await msg.answer(LANGS[lang]["no_data"])
        return
    await msg.answer_photo(BufferedInputFile(buf.getvalue(), filename="reports_24h.png"))


# ========= Mini health server (Render Web Service) =========
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


# ========= Entry =========
async def main():
    if ENABLE_HTTP:
        asyncio.create_task(run_http_server())
    print("Bot polling starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
