#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput

# Optional: creates the admin account on a fresh database when
# DJANGO_SUPERUSER_USERNAME, _EMAIL and _PASSWORD are set. Ignored if it exists.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ]; then
  python manage.py createsuperuser --noinput || true
fi
