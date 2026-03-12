# TaxNavigator AI Agent 🧭

AI-агент поддержки клиентов для **TaxNavigator & Advice B.V.**

Мультиканальный (сайт, Telegram, WhatsApp) AI-консультант по налогам,
регистрации бизнеса и бухгалтерии в Нидерландах, с особым фокусом на
украинских клиентов.

---

## Архитектура

```
┌─────────────────────────────────────────────────┐
│                  Docker Stack                    │
│                                                  │
│  ┌──────────┐  ┌────────┐  ┌──────────────────┐ │
│  │  Qdrant  │  │ Redis  │  │  Crawler (cron)  │ │
│  │ (векторы)│  │ (кэш)  │  │  индексация 9    │ │
│  │          │  │        │  │  источников      │ │
│  └────┬─────┘  └───┬────┘  └──────────────────┘ │
│       │            │                             │
│  ┌────┴────────────┴──────────────────────────┐  │
│  │          FastAPI Backend (API)              │  │
│  │  ┌──────────┐ ┌─────────┐ ┌─────────────┐  │  │
│  │  │  Intent  │ │   RAG   │ │  GPT-4o     │  │  │
│  │  │  Router  │ │ Search  │ │  Generator  │  │  │
│  │  └──────────┘ └─────────┘ └─────────────┘  │  │
│  └────────────────────────────────────────────┘  │
│       │            │            │                 │
│  ┌────┴──┐   ┌─────┴──┐  ┌─────┴──────┐         │
│  │Widget │   │Telegram│  │  WhatsApp  │         │
│  │(сайт) │   │  Bot   │  │  (Twilio)  │         │
│  └───────┘   └────────┘  └────────────┘         │
└─────────────────────────────────────────────────┘
```

## Быстрый старт

### 1. Подготовка

```bash
# Склонировать / загрузить проект на сервер
cd /path/to/taxnavigator-agent

# Создать .env из шаблона
cp .env.example .env

# Заполнить обязательные переменные:
nano .env
```

**Минимально нужно заполнить:**
- `OPENAI_API_KEY` — ключ OpenAI API
- `APP_SECRET_KEY` — случайная строка (openssl rand -hex 32)
- `ALLOWED_ORIGINS` — домен вашего сайта

### 2. Запуск через Portainer

1. Открыть Portainer → **Stacks** → **Add stack**
2. Имя: `taxnav-agent`
3. Метод: **Upload** → загрузить `docker-compose.yml`
4. Или **Repository** если проект в Git
5. В секции **Environment variables** добавить переменные из `.env`
6. **Deploy the stack**

### 3. Запуск через CLI (альтернатива)

```bash
docker-compose up -d --build
```

### 4. Проверка

```bash
# Health check
curl http://localhost:8100/health

# Тест чата
curl -X POST http://localhost:8100/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hoe registreer ik een eenmanszaak?"}'
```

### 5. Первая индексация

Crawler запустится автоматически при старте и проиндексирует все 9
источников. Это займёт 10-30 минут в зависимости от скорости сети.

Мониторинг:
```bash
docker logs -f taxnav-crawler
```

---

## Встраивание виджета на сайт

Добавить перед `</body>` на сайте taxnavigator-advice.nl:

```html
<script src="https://chat.taxnavigator-advice.nl/widget/embed.js"></script>
```

Это добавит кнопку чата в правый нижний угол сайта.

---

## Настройка Telegram бота

1. Создать бота через @BotFather в Telegram
2. Получить токен
3. Вписать в `.env`: `TELEGRAM_BOT_TOKEN=...`
4. Установить webhook:
   ```
   curl https://chat.taxnavigator-advice.nl/api/telegram/setup
   ```

---

## Настройка WhatsApp (Twilio)

1. Зарегистрироваться на twilio.com
2. Активировать WhatsApp sandbox (или полный номер)
3. Заполнить в `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`
4. В Twilio Console → настроить webhook URL:
   `https://chat.taxnavigator-advice.nl/api/whatsapp/webhook`

---

## Потребление ресурсов (примерное)

| Контейнер | RAM    | CPU  |
|-----------|--------|------|
| API       | ~200MB | low  |
| Qdrant    | ~500MB | low  |
| Redis     | ~50MB  | low  |
| Crawler   | ~200MB | periodic |
| **Итого** | **~1GB** | minimal |

---

## Автодеплой (GitHub → сервер)

Настроен webhook: при каждом `git push` в `main` сервер
автоматически подтягивает код и перезапускает контейнеры.

### Быстрая настройка (один скрипт)

```bash
# На сервере от root:
bash setup-server.sh
```

Скрипт сам: сгенерирует SSH-ключ, склонирует репо, создаст .env,
установит webhook-listener как systemd-сервис, запустит Docker-стек.

### Настройка GitHub Webhook (после setup-server.sh)

1. GitHub → Settings → **Webhooks** → Add webhook
2. **Payload URL:** `https://YOUR_DOMAIN/deploy/webhook`
3. **Content type:** `application/json`
4. **Secret:** (выведен скриптом при установке)
5. **Events:** Just the push event
6. Кнопка **Add webhook**

### Как это работает

```
git push → GitHub → POST /deploy/webhook → verify HMAC signature
                                          → git pull origin main
                                          → docker-compose build
                                          → docker-compose up -d
                                          → health check
```

### Мониторинг деплоев

```bash
# Лог деплоев
tail -f /opt/Taxnavigator_agent/deploy.log

# Статус webhook-сервиса
systemctl status taxnav-webhook
```

---

## Интеграция с Bitrix24 (этап 2)

Готовится отдельный модуль. Будет включать:
- Автоматическое создание лидов из чата
- Привязка к существующим контактам
- Логирование диалогов в CRM
- Постановка задач при эскалации

---

## Источники знаний

| # | Источник | Категория |
|---|----------|-----------|
| 1 | taxnavigator-advice.nl | Услуги компании |
| 2 | belastingdienst.nl | Налоги |
| 3 | kvk.nl | Регистрация бизнеса |
| 4 | rvo.nl | Субсидии |
| 5 | rijksoverheid.nl | Законодательство |
| 6 | nba.nl | Бухгалтерия |
| 7 | rjnet.nl | Финансовая отчётность |
| 8 | ind.nl | Статус украинцев |
| 9 | refugeehelp.nl | Поддержка украинцев |
| 10 | oecd.org | Двойное налогообложение |

---

## Файловая структура

```
taxnavigator-agent/
├── docker-compose.yml      # Оркестрация всех сервисов
├── Dockerfile              # API image
├── Dockerfile.crawler      # Crawler image
├── .env.example            # Шаблон переменных окружения
├── requirements.txt        # Python зависимости (API)
├── requirements.crawler.txt # Python зависимости (crawler)
├── app/
│   ├── main.py             # FastAPI приложение
│   ├── settings.py         # Настройки из ENV
│   ├── routers/
│   │   ├── chat.py         # POST /api/chat/ — основной API
│   │   ├── telegram.py     # Telegram webhook
│   │   ├── whatsapp.py     # WhatsApp webhook (Twilio)
│   │   ├── widget.py       # Chat widget + embed.js
│   │   └── health.py       # GET /health
│   ├── services/
│   │   ├── agent_service.py   # Мозг: intent → RAG → GPT-4o → ответ
│   │   ├── qdrant_service.py  # Работа с векторной БД
│   │   └── redis_service.py   # Сессии и rate limiting
│   └── templates/
│       └── widget.html     # Чат-виджет (HTML/CSS/JS)
├── crawler/
│   └── main.py             # Краулер + индексатор
├── config/
│   ├── sources.json        # Конфигурация 9 источников
│   └── prompts.json        # Системные промпты агента
├── webhook/
│   ├── server.py              # GitHub webhook receiver
│   └── taxnav-webhook.service # Systemd unit file
├── deploy.sh               # Скрипт деплоя
├── setup-server.sh         # Полная настройка сервера (1 раз)
└── nginx/
    └── taxnav-chat.conf    # Nginx конфигурация (пример)
```
