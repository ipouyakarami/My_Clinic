# MyClinic

A web application for booking in-person doctor appointments and managing patient
medication schedules with Telegram reminders.

**Live site:** https://web-production-f0235.up.railway.app

## Services

- **Doctor directory & profiles** — browse verified doctors by specialty and view their availability.
- **Appointments** — doctors define working hours, which are split into 30-minute slots; patients book a slot (up to 1 hour before it starts) and get an instant confirmation email. Appointments can be cancelled up to 12 hours in advance, and doctors add visit summaries after each visit.
- **Medication management** — patients build medication schedules (daily, specific weekdays, every N days, or every X hours), mark intakes as Taken/Skipped (or "all taken" in bulk), and track stock.
- **Smart reminders** — daily dose reminders and one-time refill alerts are sent by email and, for linked users, via a Telegram bot (`@myclinicmedicationreminderbot`).
- **Medical tests** — patients upload test results (PDF, max 5 MB), and doctors can view/download them through a shared appointment.

## Technology stack

- **Backend:** Django 6.1 with django-allauth (email-based auth + mandatory verification)
- **Database:** PostgreSQL
- **Background jobs & scheduling:** Celery + Celery Beat (broker/backend run over Postgres — no Redis)
- **Frontend:** Server-side Django templates, Tailwind CSS 4, flatpickr
- **Messaging:** Gmail SMTP for email; python-telegram-bot for the Telegram bot
- **Deployment:** Railway (Gunicorn web + Celery workers, see `Procfile`)

## Getting started

Prerequisites: Python 3.11+, PostgreSQL, and Node.js (for Tailwind).

```bash
# 1. Install Python dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env        # then fill in the variables (see below)

# 3. Create the database schema
python manage.py migrate

# 4. Build frontend assets
npm install
npm run build:css

# 5. Run
python manage.py runserver   # http://127.0.0.1:8000
```

### Demo data (optional)

```bash
python manage.py seed_demo
```

Creates four doctors, demo patients, medication schedules, and appointments
(only intended for local/demo use).

### Background workers

Open separate terminals (required for reminders to fire):

```bash
celery -A myclinic worker -B -l info   # dose + refill reminders
python manage.py run_telegram_bot      # Telegram bot (long-polling)
```

### Running tests

```bash
python manage.py test
```

## Environment variables

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DATABASE_URL` | PostgreSQL connection string |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | Gmail SMTP credentials |
| `DEFAULT_FROM_EMAIL` | Sender address for outgoing email |
| `TIME_ZONE` | Default: `Asia/Tehran` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_BOT_USERNAME` | Telegram bot credentials |
| `DEMO_MODE` / `DEMO_PASSWORD` (optional) | Demo account seeding |

> `.env` is gitignored — never commit secrets to version control.

## Documentation

- `requirements.md` — product & technical specification
- `plan.md` — implementation plan and changelog
