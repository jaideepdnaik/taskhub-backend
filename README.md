# TaskHub Backend

![CI](https://github.com/jaideepdnaik/taskhub-backend/actions/workflows/ci.yml/badge.svg)

Multi-tenant project & task management API built with Django REST Framework.

**Stack:** Django 6 · DRF · PostgreSQL · SimpleJWT · django-filter · drf-spectacular · Celery + Redis · pgvector · Voyage AI + Claude (Ask TaskHub) · Pytest

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
  dashboard/       # read-only stats endpoint
  assistant/       # Ask TaskHub: RAG over tasks, comments and projects
requirements/      # base.txt, dev.txt, prod.txt
```

## Local setup (Windows / PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\dev.txt
copy .env.example .env          # then set SECRET_KEY
docker compose up -d            # Postgres (with pgvector) + Redis
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- API docs: http://localhost:8000/api/docs/
- Health check: http://localhost:8000/api/health/
- Celery worker (Windows needs the solo pool): `celery -A config worker -l info -P solo`
- Tests: `pytest --cov=apps` (fails below 85% coverage; CI runs the same on every push)

## Ask TaskHub (RAG assistant)

Ask questions about your org's tasks, comments and projects and get answers with
citations, limited to what you're allowed to see. `POST /api/assistant/conversations/`
with `{"question": "..."}` (plus the `X-Org-ID` header).

1. Get a [Voyage AI](https://www.voyageai.com) key and set `VOYAGE_API_KEY` in `.env`.
2. Pick how answers are generated with `ASSISTANT_LLM_BACKEND`:
   - `cli` (the dev default): uses your logged-in Claude Code (`claude` on PATH). Local development only.
   - `api`: the Anthropic API. Set `ANTHROPIC_API_KEY`. Required in production.
3. Start a Celery worker (indexing runs there), then index existing data once:
   `python manage.py reindex_knowledge`. New and edited items are indexed automatically.

If you switched an existing database volume from `postgres:17` to `pgvector/pgvector:pg17`
and see "collation version mismatch", run for each database (`template1`, `postgres`, `taskhub`):
`docker compose exec db psql -U taskhub -d <db> -c "ALTER DATABASE <db> REFRESH COLLATION VERSION;"`
and then `REINDEX DATABASE taskhub;`.
