#!/bin/bash
# Worker запускается из той же папки что и backend
# Railway: установить root directory = backend для этого сервиса
exec celery -A app.core.celery_app worker --beat --loglevel=info -Q default,analysis
