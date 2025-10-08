# RedditStatusCheckerBot

Простой Telegram-бот, который показывает текущий статус Reddit и график сбоев.

## 🚀 Установка (через Render)

1. Зайди в свой GitHub и создай новый репозиторий `RedditStatusCheckerBot`.
2. Залей туда эти файлы (`main.py`, `requirements.txt`, `Procfile`, `README.md`).
3. На сайте [render.com](https://render.com):
   - Нажми **New → Web Service**
   - Выбери этот GitHub репозиторий
   - В Environment Variables добавь:
     ```
     BOT_TOKEN=твой токен из BotFather
     ```
   - Нажми **Deploy**
4. Через пару минут бот будет работать 🎉

## 💬 Команды
- `/status` — текущее состояние Reddit  
- `/graph` — график сбоев (по накопленным данным)  
- `/start` — помощь

## 🔔 Автоуведомления
Бот каждые 5 минут проверяет Reddit и публикует уведомление
в Telegram-канал `@RedditStatusCheckerChannel` (можно поменять ID или убрать).
