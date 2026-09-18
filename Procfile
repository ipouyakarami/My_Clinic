web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn myclinic.wsgi:application --workers 2 --timeout 120 --bind 0.0.0.0:$PORT
worker: celery -A myclinic worker -B --concurrency 2 -l info
beat: python manage.py run_telegram_bot