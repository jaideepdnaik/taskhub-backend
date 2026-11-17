# TaskHub Backend

Multi-tenant project & task management API built with Django REST Framework.

**Stack:** Django 6 · DRF · PostgreSQL · SimpleJWT · django-filter · drf-spectacular · Celery + Redis · Pytest

## Project layout

```
config/            # project config
  settings/        # base.py, dev.py, test.py, prod.py
  celery.py        # Celery app
apps/
  core/            # shared base models, health check
  accounts/        # custom email-based User, auth
  organizations/   # tenants, memberships, roles, invites
  projects/        # projects scoped to an organization
  tasks/           # tasks, comments, activity log
  notifications/   # in-app + email notifications (Celery)
requirements/      # base.txt, dev.txt, prod.txt
```

## Local setup (Windows / PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\dev.txt
copy .env.example .env          # then set SECRET_KEY
docker compose up -d            # Postgres + Redis
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- API docs: http://localhost:8000/api/docs/
- Health check: http://localhost:8000/api/health/
- Celery worker (Windows needs the solo pool): `celery -A config worker -l info -P solo`
- Tests: `pytest --cov=apps`
