# TaskHub Backend

Multi-tenant project & task management API (like a lightweight Jira/Asana). Each
**Organization** is a tenant with its own projects, tasks and members. Users in one
org must never see another org's data. The React frontend lives in a separate repo
(`taskhub-frontend`) and talks to this API over JWT.

## Stack
Django 6 · Django REST Framework · PostgreSQL · SimpleJWT · django-filter ·
drf-spectacular · Celery + Redis · Pytest + factory_boy · Docker, Gunicorn, Nginx, AWS

## Commands (Windows / PowerShell)
- Activate venv: `.\.venv\Scripts\Activate.ps1`
- Services: `docker compose up -d` (Postgres + Redis)
- Run server: `python manage.py runserver`
- Migrations: `python manage.py makemigrations <app>` then `python manage.py migrate`
- Tests: `pytest` · with coverage: `pytest --cov=apps --cov-report=term-missing`
- Celery worker: `celery -A config worker -l info -P solo` (solo pool is required on Windows)
- Celery beat: `celery -A config beat -l info`
- OpenAPI schema: `python manage.py spectacular --file schema.yml --validate`

## Layout
- `config/settings/` holds `base.py`, `dev.py`, `test.py` and `prod.py`. Config comes from `.env` via django-environ.
- `config/celery.py`: the Celery app. Tasks go in each app's `tasks.py`.
- `apps/core/`: `TimeStampedModel`, shared mixins, permissions and pagination.
- `apps/accounts/`: a custom `User` that logs in with email (no username). Always use `get_user_model()` or `settings.AUTH_USER_MODEL`.
- `apps/organizations/`: Organization, Membership (role) and Invitation.
- `apps/projects/`, `apps/tasks/` (Task, Comment, ActivityLog) and `apps/notifications/`.
- Each app has `models.py`, `serializers.py`, `views.py`, `urls.py`, `permissions.py` (if needed), `filters.py` (if needed) and `tests/`.

## Domain model
- **Organization**: name, slug, owner.
- **Membership**: user, organization, role. Roles are `owner`, `admin`, `member` and `viewer`. Unique on (user, organization).
- **Invitation**: organization, email, role, token, expires_at, accepted_at.
- **Project**: organization, name, description, members (M2M), created_by, is_archived.
- **Task**: project, title, description, status (`todo`/`in_progress`/`review`/`done`), priority (`low`/`medium`/`high`/`urgent`), assignee, reporter, due_date, labels (ArrayField), position.
- **Comment**: task, author, body.
- **ActivityLog**: task, actor, action, changes (JSON).
- **Notification**: recipient, type, payload (JSON), is_read.
- All models extend `TimeStampedModel`.

## Multi-tenancy rules (critical)
- The frontend sends the current org in the `X-Org-ID` header. Resolve it once, in a mixin or middleware, and confirm the user is a member. Return 403 or 404 otherwise.
- **Every queryset must be filtered to the current org.** Never use `Model.objects.all()` in a view. Override `get_queryset()`.
- Set `organization` on the server side when creating objects. Never accept it from the request body.
- When you return objects from another org, return a 404, not a 403. That way you don't reveal that they exist.
- Every new endpoint needs a test proving that a user from org B can't list, read, update or delete org A's data.

## Permissions
- Object-level permission classes go in `permissions.py`: `IsOrgMember`, `IsOrgAdmin`, `IsProjectMember` and `CanEditTask`.
- Viewer is read-only. Member can create and edit tasks in their projects. Admin and Owner manage projects and members. Only Owner can delete the org.
- The default is `IsAuthenticated`. Only health and auth endpoints are public.

## API conventions
- Use ViewSets with routers, under the `/api/` prefix, with plural nouns. Nest tasks under projects: `/api/projects/{id}/tasks/`.
- Non-CRUD actions use `@action`, for example `PATCH /api/tasks/{id}/move/` for status plus position.
- Use django-filter `FilterSet` classes for filtering, plus `SearchFilter` and `OrderingFilter`. Pagination is page-number with `PAGE_SIZE=20`.
- Use separate read and write serializers when the shapes differ. Return nested summaries, such as assignee name and email, on reads.
- Avoid N+1 queries: use `select_related` and `prefetch_related` in `get_queryset()`.
- Add `@extend_schema` wherever drf-spectacular can't infer the request or response shape. Keep the schema free of warnings.
- Errors use DRF's standard format. Don't invent custom error envelopes.

## Celery
- Tasks must be idempotent, take IDs (never model instances), and use `autoretry_for` with backoff for network or email errors.
- Trigger tasks with `transaction.on_commit(lambda: task.delay(id))`.
- Scheduled jobs (due-date reminders, weekly summaries) go in `CELERY_BEAT_SCHEDULE`.

## Testing
- Use pytest and pytest-django with factory_boy factories, in `apps/<app>/tests/factories.py`. Shared fixtures go in the root `conftest.py`.
- Test models, permissions and API endpoints, including tenant isolation. Aim for coverage of at least 85%.
- In tests, Celery runs tasks immediately (`CELERY_TASK_ALWAYS_EAGER`) and email goes to the in-memory `locmem` backend.

## Workflow
- Work in small steps, one feature at a time. Models, then serializers, then views and URLs, then tests.
- Run `pytest` before saying a task is done, and create migrations for any model change.
- Don't commit `.env`, secrets, `db.sqlite3` or `.venv`.
- Write commit messages in the imperative mood and keep each commit to one feature.

## Roadmap
- [x] 0. Project setup: settings, custom User, JWT, Swagger docs, Celery, Pytest
- [ ] 1. Organizations, memberships, invitations, org scoping and permissions, tenant-isolation tests
- [ ] 2. Registration endpoint, `/api/auth/me/`, list my organizations
- [ ] 3. Projects CRUD with members and archive
- [ ] 4. Tasks CRUD with filters, search, ordering, pagination and the `move` action
- [ ] 5. Comments and the activity log
- [ ] 6. Notifications: Celery emails, a Beat job for due-date reminders, an in-app notifications API
- [ ] 7. Dashboard stats endpoint, coverage of at least 85%, GitHub Actions CI
- [ ] 8. Production: Dockerfile, Gunicorn, Nginx, AWS (EC2 + RDS + ElastiCache), HTTPS
