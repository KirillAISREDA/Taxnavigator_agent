# CLAUDE.md — TaxNavigator AI Agent

## Project Overview
AI customer support agent for TaxNavigator & Advice B.V. (Netherlands).
Multi-channel (website widget, Telegram, WhatsApp) assistant for tax,
business registration, and accounting queries. Special focus on Ukrainian clients.

## Tech Stack
- **Backend:** Python 3.11, FastAPI, uvicorn
- **LLM:** OpenAI GPT-4o (via openai SDK)
- **Vector DB:** Qdrant (RAG retrieval)
- **Cache:** Redis (sessions, rate limiting)
- **Crawler:** BeautifulSoup + scheduled re-indexing of 9 Dutch gov sources
- **Deploy:** Docker Compose, Portainer, GitHub Actions auto-deploy (SSH)
- **Frontend:** Vanilla HTML/CSS/JS chat widget (embedded via iframe)

## Project Structure
```
├── app/                    # FastAPI application
│   ├── main.py             # App entrypoint, lifespan, middleware
│   ├── settings.py         # Pydantic settings from .env
│   ├── routers/
│   │   ├── chat.py         # POST /api/chat/ — main chat endpoint
│   │   ├── telegram.py     # Telegram webhook handler
│   │   ├── whatsapp.py     # WhatsApp/Twilio webhook handler
│   │   ├── widget.py       # Chat widget page + embed.js
│   │   └── health.py       # GET /health
│   ├── services/
│   │   ├── agent_service.py   # Core: lang detect → intent → RAG → GPT-4o → response
│   │   ├── qdrant_service.py  # Vector search operations
│   │   └── redis_service.py   # Session management
│   └── templates/
│       └── widget.html     # Chat widget UI
├── crawler/
│   └── main.py             # Web crawler + Qdrant indexer (runs on schedule)
├── config/
│   ├── sources.json        # 9 knowledge sources configuration
│   └── prompts.json        # System prompts (NL/UK/RU/EN)
├── .github/workflows/
│   └── deploy.yml          # GitHub Actions auto-deploy (SSH → server)
├── docker-compose.yml      # Qdrant + Redis + API + Crawler
├── Dockerfile              # API container
├── Dockerfile.crawler      # Crawler container
├── deploy.sh               # Auto-deploy script
├── setup-server.sh         # One-time server setup
└── nginx/taxnav-chat.conf  # Nginx reverse proxy config
```

## Key Commands
```bash
# Run locally (dev)
docker-compose up -d --build

# Check health
curl http://localhost:8100/health

# Test chat API
curl -X POST http://localhost:8100/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hoe registreer ik een BV?"}'

# View crawler logs
docker logs -f taxnav-crawler

# Rebuild single service
docker-compose up -d --build --no-deps api

# Manual deploy on server (GitHub Actions does this automatically)
bash deploy.sh
```

## Environment Variables
Required in `.env` (copy from `.env.example`):
- `OPENAI_API_KEY` — OpenAI API key (required)
- `APP_SECRET_KEY` — random string for sessions
- `ALLOWED_ORIGINS` — CORS origins (your domain)
- `TELEGRAM_BOT_TOKEN` — for Telegram channel
- `TWILIO_*` — for WhatsApp channel
- `BITRIX_WEBHOOK_URL` — for CRM integration (phase 2)

## Architecture: Message Flow
1. User sends message (any channel)
2. `agent_service.detect_language()` → nl/uk/ru/en
3. `agent_service.classify_intent()` → GPT-4o-mini classifies into category
4. `qdrant_service.search()` → retrieves relevant chunks from knowledge base
5. GPT-4o generates response with RAG context
6. Check if escalation to human is needed
7. Save to Redis session history
8. Return response to channel

## Agent Behavior Rules
- INFORMATIVE, not advisory — explains procedures, never gives specific tax calculations
- Detects Ukrainian clients automatically and applies special context (residence status, work rights, double taxation treaty)
- Escalates to human specialist for: specific calculations, complex structures, international tax, legal disputes
- Responds in the user's language (auto-detected)
- Always mentions the option to book a consultation for complex topics

## Knowledge Sources
1. taxnavigator-advice.nl — company services
2. belastingdienst.nl — Dutch tax authority
3. kvk.nl — Chamber of Commerce (business registration)
4. rvo.nl — Government subsidies
5. rijksoverheid.nl — Tax legislation
6. nba.nl — Professional accounting standards
7. rjnet.nl — Dutch financial reporting standards
8. ind.nl — Immigration (Ukrainian status/work rights)
9. refugeehelp.nl — Ukrainian support portal
10. oecd.org — Double taxation treaties UA-NL

## Code Style
- Python 3.11+, type hints everywhere
- async/await for all I/O operations
- structlog for logging
- Pydantic models for request/response validation
- Config in .env, never hardcode secrets

## GitHub Repository
https://github.com/KirillAISREDA/Taxnavigator_agent
Branch: main
