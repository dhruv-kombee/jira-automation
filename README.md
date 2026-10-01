# Jira Automation — Teams Integration (Python)

**MVP Phase 1**: Prove that the Python backend can receive Microsoft Teams messages through Microsoft Graph, identify the sender, and store the normalized message.

## Architecture (Phase 1)

```
Microsoft Teams
      ↓
Microsoft Graph (Change Notifications)
      ↓
FastAPI Backend (/webhooks/teams)
      ↓
Message Service (fetch, normalize, identify)
      ↓
SQLite Database (persistence)
```

## Quick Start

### 1. Prerequisites

- Python 3.10+ installed
- A Microsoft 365 tenant with Teams
- A Microsoft Entra (Azure AD) application (see [Setup Guide](docs/TEAMS_MVP_SETUP.md))
- A secure tunnel for local development (e.g., ngrok or cloudflared)

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your Microsoft Entra and Teams values
```

See [docs/GET_ALL_ENV_CREDENTIALS.md](docs/GET_ALL_ENV_CREDENTIALS.md) for a complete step-by-step guide to finding and obtaining every `.env` value, or [docs/TEAMS_MVP_SETUP.md](docs/TEAMS_MVP_SETUP.md) for the architecture setup guide.

### 4. Start (All-in-One Command) 🚀

Run **one command** to start ngrok tunnel, verify/activate Graph subscription, launch the FastAPI engine, and open the real-time Dashboard:

```bash
py run.py
```

Your browser will automatically open to [http://localhost:3000](http://localhost:3000) showing:
- **Live System Health**: FastAPI status, public ngrok forwarding, SQLite WAL persistence.
- **Interactive Subscription Monitor**: Real-time countdown timer with auto-renewal.
- **Live Teams Message Feed**: Real-time incoming messages with role badges (Client / PM / Developer).
- **Interactive Simulator**: Test message detection and role identification without opening Teams.
- **Payload Inspector**: View normalized JSON payloads with one click.

Alternatively, you can run the server directly:
```bash
py main.py
```

### 7. Test

Send a message in the configured Teams channel. The backend will log the message details.

See [docs/TEAMS_MVP_TESTING.md](docs/TEAMS_MVP_TESTING.md) for complete testing instructions.

You can also run unit tests:

```bash
pytest tests/test_basic.py
```

## Project Structure

```
Jira-Automation/
├── main.py                           # Server entry point (FastAPI / Uvicorn)
├── requirements.txt                  # Python dependencies
├── .env.example                      # Environment variable template
├── .gitignore
│
├── src/
│   ├── app.py                        # FastAPI application setup + middleware
│   ├── config.py                     # Centralized environment configuration
│   ├── logger.py                     # Structured colorized logger
│   ├── database.py                   # SQLite connection & table initialization
│   ├── graph_client.py               # Microsoft Graph client (MSAL + HTTPX)
│   │
│   ├── routes/
│   │   ├── health.py                 # Health and messages inspection routes
│   │   └── webhooks.py               # Microsoft Graph change notification webhook
│   │
│   ├── services/
│   │   ├── message_service.py        # Message processing orchestrator
│   │   └── sender_service.py         # Sender role identification
│   │
│   └── repositories/
│       └── message_repository.py     # Database operations (swap-friendly)
│
├── scripts/
│   └── manage_subscription.py        # CLI for Graph subscription management
│
├── tests/
│   └── test_basic.py                 # Unit and endpoint tests
│
├── docs/
│   ├── TEAMS_MVP_SETUP.md            # Detailed setup guide
│   ├── TEAMS_MVP_TESTING.md          # Testing procedures
│   └── GET_ALL_ENV_CREDENTIALS.md    # Credentials acquisition guide
│
└── data/                             # SQLite database (gitignored)
    └── messages.db
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/messages` | List stored messages (dev inspection) |
| `GET` | `/webhooks/teams` | Graph subscription validation |
| `POST` | `/webhooks/teams` | Graph change notifications (202 Accepted + background processing) |

## CLI Commands

| Command | Description |
|---------|-------------|
| `python main.py` | Start the FastAPI server |
| `python scripts/manage_subscription.py create` | Create a Graph subscription |
| `python scripts/manage_subscription.py list` | List active subscriptions |
| `python scripts/manage_subscription.py renew <id>` | Renew a subscription |
| `python scripts/manage_subscription.py delete <id>` | Delete a subscription |

## Technology Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| Runtime | Python 3.10+ | Fast development, rich AI & automation ecosystem |
| Framework | FastAPI + Uvicorn | High-performance async ASGI web framework |
| Auth | MSAL Python (`msal`) | Official Microsoft Authentication Library with token caching |
| HTTP Client | HTTPX | High-performance async/sync HTTP client |
| Database | SQLite (`sqlite3`) | Zero-config MVP persistence with WAL mode |
| Logging | Standard logging + structured format | Colorized, structured, and production-ready |

## Phase 1 Scope

### ✅ Implemented
- Microsoft Graph authentication (client credentials flow via MSAL)
- Graph subscription creation/renewal/deletion CLI
- Webhook endpoint for Graph change notifications
- Subscription validation handling (GET & POST echo)
- Teams message retrieval from Graph API
- Message normalization (ID, sender, text, timestamps, URL, attachments)
- Sender role identification (by configured user IDs)
- SQLite persistence with duplicate protection
- Structured logging for all events
- Error handling for Graph API failures

### ❌ Not Implemented (Future Phases)
- Jira integration / MCP
- LLM issue classification
- PM reaction approval
- Duplicate issue detection
- Reminders
- Dashboard
- Production database (PostgreSQL)

## License

ISC
