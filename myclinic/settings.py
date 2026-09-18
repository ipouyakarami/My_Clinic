"""
Django settings for MyClinic.

All credentials load from environment variables (.env in dev).
See requirements.md §2 — never hardcode secrets here.
"""

import datetime
import os
from pathlib import Path

import dj_database_url
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(
            f"Required environment variable {name} is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


SECRET_KEY = require_env("DJANGO_SECRET_KEY")

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"  # flip off (DJANGO_DEBUG=false) for deployment

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
    ALLOWED_HOSTS.append(os.environ["RAILWAY_PUBLIC_DOMAIN"])
    CSRF_TRUSTED_ORIGINS = [f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"]

# Railway terminates TLS at its edge and forwards plain HTTP to the container,
# so tell Django to trust the X-Forwarded-Proto header from the proxy.
# Without this, request.is_secure() is False and the browser's "https://" Origin
# fails Django's CSRF origin check (surfacing as a bare 403 on form POSTs).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third-party
    "allauth",
    "allauth.account",
    # Local apps
    "core",
    "accounts",
    "appointments",
    "medical_tests",
    "medications",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ROOT_URLCONF = "myclinic.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "myclinic.wsgi.application"

# Database — PostgreSQL only, via DATABASE_URL (requirements.md §2)
require_env("DATABASE_URL")
DATABASES = {
    "default": dj_database_url.config(conn_max_age=600),
}

# Custom user model (accounts.User) — must exist before first migrate
AUTH_USER_MODEL = "accounts.User"

# django-allauth — email-only accounts, mandatory verification
ACCOUNT_ADAPTER = "accounts.adapter.AccountAdapter"
ACCOUNT_FORMS = {
    "login": "accounts.forms.MyClinicLoginForm",
    "reset_password": "accounts.forms.MyClinicResetPasswordForm",
    "reset_password_from_key": "accounts.forms.MyClinicResetPasswordKeyForm",
}

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_RATE_LIMITS = {"confirm_email": None}

LOGIN_REDIRECT_URL = "/dashboard/"
ACCOUNT_LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization — English UI, Tehran time, Gregorian/UTC storage
LANGUAGE_CODE = "en"

TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Tehran")

USE_I18N = True

USE_TZ = True

# Static & media files (local filesystem storage)
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

if not DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email — print to the server/console logs. No real SMTP in use.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "MyClinic <noreply@myclinic.local>")

# Base URL for building absolute links in emails sent outside a request context
SITE_URL = os.environ.get(
    "SITE_URL",
    f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    else "http://localhost:8000",
)

# Demo/dev-only convenience: password used by the seeded demo accounts and shown
# on the login page's "Demo accounts" panel. Change via DEMO_PASSWORD env if you
# re-seed with a different password; the login panel reads the same value.
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "demo1234")

# Telegram bot — token from @BotFather, username from the bot's info page
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "myclinicmedicationreminderbot")

# Celery — broker + result backend over PostgreSQL via Kombu's SQLAlchemy
# transport. No separate Redis service: the same DATABASE_URL that backs the
# app's data also queues tasks and stores results, using the sqla+/db+ scheme
# prefixes. (No task in this project reads results, so the backend is
# capability parity, not load-bearing.)
# dj_database_url emits the legacy "postgres://" scheme; SQLAlchemy requires
# "postgresql://" and, since this project uses psycopg (v3) not psycopg2, the
# dialect must be "postgresql+psycopg://".
_sqla_db_url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
CELERY_BROKER_URL = f"sqla+{_sqla_db_url}"
CELERY_RESULT_BACKEND = f"db+{_sqla_db_url}"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = False

CELERY_BEAT_SCHEDULE = {
    "extend-intake-window-daily": {
        "task": "medications.tasks.extend_intake_window",
        "schedule": datetime.timedelta(days=1),
    },
    "send-medication-reminders-every-minute": {
        "task": "medications.tasks.send_reminder_emails",
        "schedule": crontab(minute="*"),
    },
}
