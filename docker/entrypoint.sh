#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0

exec "$@"
