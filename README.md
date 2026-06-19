# Gould Construction APM (Assistant Project Manager)

AI-powered Assistant Project Manager system for Gould Construction that automates project management workflows using data from **HCSS HeavyJob** and other systems.

## Modules

- **Time Log Verification**: Daily timecard validation with automated email reports
- **Schedule Monitoring**: Real-time project schedule tracking with alerts for delays and budget overruns
- **Quantity Tracking**: Monitor installed quantities vs contract requirements
- **Cost Monitoring**: Track costs vs budget with critical loss detection
- **Billing Management**: Automated billing draft generation and tracking

---

## Project Structure

```
├── api/                        # FastAPI backend
│   ├── main.py                 # App entry point, all routes registered here
│   ├── config.py               # All settings loaded from .env
│   ├── schemas.py              # Pydantic request/response models
│   ├── database.py             # SQLAlchemy engine + User model (PostgreSQL)
│   ├── auth.py                 # JWT creation, password hashing, auth dependency
│   ├── logger.py               # Structured logging (JSON in prod, coloured in dev)
│   ├── middleware.py           # Request ID tracing + access logging
│   └── routes/
│       ├── auth_routes.py      # POST /api/auth/signup, /api/auth/login, GET /api/auth/me
│       └── notify_routes.py    # POST /api/notify/send — manual email trigger
│
├── payroll_verification/       # Core verification engine
│   ├── hcss_client.py          # HCSS HeavyJob API client (auth + all data fetching)
│   ├── verifier.py             # Applies all 7 verification rules to each timecard
│   └── reporting.py           # Groups results, formats CSV/JSON output
│
├── schedule_monitoring/        # Schedule monitoring system (NEW)
│   ├── hcss_schedule_client.py # HCSS API client for schedule data
│   ├── schedule_analyzer.py    # Schedule analysis and alert generation
│   ├── monitor.py              # Main orchestration module
│   ├── notifier.py             # Email notifications for alerts
│   ├── run_monitor.py          # CLI runner script
│   ├── example_usage.py        # Example usage demonstrations
│   └── README.md               # Detailed schedule monitoring documentation
│
├── notifications/              # Email system
│   ├── email_sender.py         # SMTP email sending via Gmail
│   └── email_template.py       # HTML email report builder
│
├── scheduler/                  # Automated daily job
│   └── daily_job.py            # Runs verification + sends email at 1:00 PM MT daily
│
├── quantity_tracking/          # Dev/test scripts for HCSS API exploration
│   ├── get_token.py
│   ├── get_jobs.py
│   ├── get_cost_codes.py
│   ├── get_business_units.py
│   ├── get_quantity_progress.py
│   ├── get_job_costs_to_date.py
│   └── time_cards.py
│
├── HOW_IT_WORKS.md             # Plain-English explanation of the full system
├── .env                        # Credentials and config (never committed)
├── .env.example                # Template showing all required env vars
└── requirements.txt            # All Python dependencies
```

---

## Verification Rules

For each timecard (per foreman, per day):

### 🔴 REJECTED — Hard Failures
Timecard **cannot be processed for time log** until fixed:

| # | Rule | Condition |
|---|------|-----------|
| 1 | Missing Labor Hours | Total hours across all employees = 0 |
| 2 | Missing Quantities | A cost code has hours logged but quantity = 0 |
| 3 | Missing Diary Entry | No diary text found AND no cost code notes present |

### 🟡 FLAGGED — Soft Warnings
Timecard passed hard checks but needs review (only checked if not rejected):

| # | Rule | Condition |
|---|------|-----------|
| 1 | Quantity Without Labor | A cost code has quantity > 0 but hours = 0 |
| 2 | No Photos Attached | No photos uploaded for this job/foreman/date |
| 3 | Late Submission | Submitted after deadline (next day 1 PM; Mon 1 PM for Fri/Sat) |
| 4 | Missing Subcontractor Info | Job has subcontract items but no transactions recorded |

### 🟢 APPROVED
Passed all 7 rules — ready for time log processing.

---

## Schedule Monitoring System

The schedule monitoring system tracks project progress and generates alerts for:

- **Quantity Progress**: Alerts when work items reach 75% of budgeted quantities
- **Cost vs Budget**: Alerts when costs reach 75% of budget, critical alerts for 15%+ losses  
- **Schedule Duration**: Detects delays and projects past baseline completion dates
- **Seasonal Risks**: Warns about asphalt work past September 30 and concrete work past October 15

### Quick Start

```bash
# Monitor a specific job
python -m schedule_monitoring.run_monitor \
    --job-id "job-123" \
    --business-unit-id "bu-456"

# Monitor all active jobs
python -m schedule_monitoring.run_monitor \
    --business-unit-id "bu-456" \
    --all-jobs

# Save report to file
python -m schedule_monitoring.run_monitor \
    --job-id "job-123" \
    --business-unit-id "bu-456" \
    --output report.json
```

### Python API

```python
from schedule_monitoring.monitor import ScheduleMonitor

# Initialize monitor
monitor = ScheduleMonitor(threshold_percent=75.0)

# Monitor a job
report = monitor.monitor_job(
    job_id="job-123",
    business_unit_id="bu-456",
)

# Check for critical alerts
if report["summary"]["critical_alerts"] > 0:
    print(f"CRITICAL: {report['summary']['critical_alerts']} alerts!")
```

**Full documentation**: See [schedule_monitoring/README.md](schedule_monitoring/README.md)

---

## API Endpoints

Base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check — returns version |

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/signup` | Register new user → returns JWT token |
| POST | `/api/auth/login` | Login → returns JWT token |
| GET | `/api/auth/me` | Get current user profile (requires token) |

### Time Log Verification
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/verify` | Full verification — summary + all timecards |

`/api/verify` query parameters (all optional):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `date` | `YYYY-MM-DD` | yesterday | Single date to verify |
| `date_from` | `YYYY-MM-DD` | yesterday | Range start (triggers range mode) |
| `date_to` | `YYYY-MM-DD` | yesterday | Range end (triggers range mode) |
| `business_unit_id` | string | — | Filter by business unit |

**Single date** — pass `date` or omit entirely (defaults to yesterday). Returns `VerifyResponse`:
```json
{
  "summary": { "date": "2026-04-23", "rejected": 2, "flagged": 3, "approved": 10, "total": 15 },
  "results": [{ "id": "...", "date": "2026-04-23", "job_code": "JOB-001", "foreman": "John Smith",
                "status": "REJECTED", "reasons": ["Missing labor hours"], "flags": [], "why": "..." }]
}
```

**Date range** — pass `date_from` and/or `date_to`. Returns `RangeVerifyResponse`:
```json
{
  "summary": { "date_from": "2026-04-14", "date_to": "2026-04-21", "rejected": 5, "flagged": 8, "approved": 40, "total": 53 },
  "by_date": [{ "date": "2026-04-14", "rejected": 1, "flagged": 2, "approved": 5, "total": 8 }, "..."],
  "results": [{ "..." }]
}
```

> `reasons` and `flags` are returned as **arrays of strings**, not semicolon-joined strings.

### Notifications
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/notify/send` | Run verification and send email report immediately |

---

## Auth Flow

```
1. POST /api/auth/signup  { email, password, full_name }  →  { access_token, user }
2. POST /api/auth/login   { email, password }              →  { access_token, user }
3. All protected requests → Header: Authorization: Bearer <token>
4. GET /api/auth/me                                        →  { id, email, full_name }
```

Password rules: minimum 8 characters, must contain at least one letter and one number.  
Tokens expire after 60 minutes (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`).

---

## Setup

### 1. Clone and create virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in all values:

```env
# HCSS API credentials
HCSS_CLIENT_ID=your_client_id
HCSS_CLIENT_SECRET=your_client_secret
HCSS_TIMEOUT_SECONDS=120

# PostgreSQL (Supabase session pooler recommended)
DATABASE_URL=postgresql://user:password@host:5432/postgres

# JWT Auth
SECRET_KEY=your-long-random-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Email notifications (Gmail SMTP)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your@gmail.com
SMTP_PASSWORD=your_app_password
EMAIL_RECIPIENTS=recipient1@mail.com,recipient2@mail.com
SMTP_FROM_NAME=Gould APM Bot

# API behaviour
LOG_FORMAT=text          # "json" for production
LOG_LEVEL=INFO
ALLOWED_ORIGINS=*        # Lock down in production
DOCS_ENABLED=true        # Set to "false" in production
```

---

## Running the App

### FastAPI Backend

```bash
uvicorn api.main:app --reload
```

The database tables are created automatically on first startup.

### Daily Email Scheduler

Runs verification every day at **1:00 PM US Mountain Time** and sends the HTML report to all `EMAIL_RECIPIENTS`:

```bash
python -m scheduler.daily_job
```

---

## Database

Uses **PostgreSQL** via SQLAlchemy. Recommended: [Supabase](https://supabase.com) free tier.

- Use the **Session mode (Pooler)** connection string from Supabase
- Tables are auto-created on startup via `create_tables()`
- Current tables: `users` (id, email, full_name, hashed_password, is_active, created_at)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI + Uvicorn |
| Database | PostgreSQL (Supabase) via SQLAlchemy + psycopg3 |
| Auth | JWT (python-jose) + bcrypt password hashing (passlib) |
| Data Source | HCSS HeavyJob REST API v1 |
| Email | SMTP via Gmail + HTML templates |
| Scheduler | APScheduler (blocking, cron trigger) |
| Validation | Pydantic v2 |
| Logging | Structured — coloured text (dev) / JSON (prod) |

---

## Key Design Decisions

- **SHA-256 pre-hashing** before bcrypt — avoids the 72-byte bcrypt limit safely
- **Token caching** in HCSSClient — HCSS access token is reused until 60s before expiry
- **Result caching** in verifier — diary/photo data fetched once per job/foreman, reused across timecards
- **Cursor-based pagination** — all HCSS list endpoints follow `nextCursor` to retrieve all records
- **`pool_pre_ping=True`** on DB engine — reconnects automatically if Supabase drops idle connections

---

---

## Code Quality & Security Notes

- **`SECRET_KEY` is required** — the app will refuse to start if `SECRET_KEY` is missing from `.env`. Generate one with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- **No internal errors leaked** — signup/login endpoints return generic messages on failure; details are logged server-side only.
- **`datetime.utcnow()` replaced** — all timestamps use timezone-aware `datetime.now(timezone.utc)` (Python 3.12+ compatible).
- **`status` variable shadowing fixed** — verifier uses `tc_status` internally to avoid shadowing any future `status` import.
- **Exception handling deduplicated** — a single `_map_verifier_exception()` helper handles all HCSS error mapping in `main.py`.
- **CSV export safe on empty results** — `results_to_csv_bytes()` uses a fixed column schema derived from the dataclass, so an empty result set still produces a valid CSV with headers.
- **`reasons` and `flags` are `list[str]`** in the API response — no longer semicolon-joined strings.
- **`import json` moved to module level** in `logger.py` — no longer re-imported on every log call.
- **`HCSSClient` token reuse** — a single client instance is created per API request and passed through to the verifier. For range queries spanning multiple days, the HCSS access token is fetched once and reused across all dates (cached on the client instance until 60s before expiry).

---

For a plain-English explanation of how the system works end-to-end, see [HOW_IT_WORKS.md](HOW_IT_WORKS.md).
