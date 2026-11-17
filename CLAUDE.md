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
- Services: `docker compose up -d` (Postgres on 5432, Redis on host port **6380**, because 6379 is taken by another local Redis)
- Run server: `python manage.py runserver`
- Migrations: `python manage.py makemigrations <app>` then `python manage.py migrate`
- Tests: `pytest` · with coverage: `pytest --cov=apps --cov-report=term-missing`
- Celery worker: `celery -A config worker -l info -P solo` (solo pool is required on Windows)
- Celery beat: `celery -A config beat -l info`
- OpenAPI schema: `python manage.py spectacular --file schema.yml --validate`

## Layout
- `config/settings/` holds `base.py`, `dev.py`, `test.py` and `prod.py`. Config comes from `.env` via django-environ.
- `config/celery.py`: the Celery app. Tasks go in each app's `tasks.py`.
- `apps/core/`: `TimeStampedModel` (`models.py`), tenant scoping (`tenancy.py`) and shared org-role permissions (`permissions.py`).
- `apps/accounts/`: a custom `User` that logs in with email (no username). Always use `get_user_model()` or `settings.AUTH_USER_MODEL`.
- `apps/organizations/`: Organization, Membership (role) and Invitation.
- `apps/projects/`, `apps/tasks/` (Task, Comment, ActivityLog) and `apps/notifications/`.
- Each app has `models.py`, `serializers.py`, `views.py`, `urls.py`, `permissions.py` (if needed), `filters.py` (if needed) and `tests/`.

## Domain model
- **Organization**: name, slug (auto-generated and unique), owner (`PROTECT`), members (M2M through Membership).
- **Membership**: user, organization, role. Roles are `owner`, `admin`, `member` and `viewer` (the `Role` choices; `ADMIN_ROLES` and `EDITOR_ROLES` group them). Unique on (user, organization). Helpers: `is_admin`, `can_edit`.
- **Invitation**: organization, email (stored lowercase), role, token (auto, random), invited_by, expires_at (default 7 days), accepted_at. There's at most one pending invite per (organization, email), and the role can never be `owner`. Helpers: `is_expired`, `is_pending`. Re-inviting refreshes the existing pending invite instead of creating a new one.
- **Project**: organization, name, description, members (M2M), created_by, is_archived.
- **Task**: project, title, description, status (`todo`/`in_progress`/`review`/`done`), priority (`low`/`medium`/`high`/`urgent`), assignee, reporter, due_date, labels (ArrayField), position.
- **Comment**: task, author, body.
- **ActivityLog**: task, actor, action, changes (JSON).
- **Notification**: recipient, type, payload (JSON), is_read.
- All models extend `TimeStampedModel`.

## Multi-tenancy rules (critical)
- The frontend sends the current org in the `X-Org-ID` header. Every org-scoped view uses `OrgScopedMixin` from `apps/core/tenancy.py`, placed **before** the DRF base class. The mixin:
  - returns 401 if the user isn't logged in, 400 if the header is missing or malformed, and 404 if the org doesn't exist or the user isn't a member;
  - exposes `self.organization` and `self.membership`, also available as `request.organization` and `request.membership`;
  - filters `get_queryset()` with the `org_lookup` path (default `"organization"`; set something like `"project__organization"` for indirect models and override `perform_create`);
  - sets `organization` in `perform_create()`.
- Add `@extend_schema(parameters=[ORG_HEADER_PARAMETER])` to org-scoped views so the header shows up in the docs.
- **Every queryset must be filtered to the current org.** Never use `Model.objects.all()` in a view. Override `get_queryset()`.
- Set `organization` on the server side when creating objects. Never accept it from the request body.
- When you return objects from another org, return a 404, not a 403. That way you don't reveal that they exist.
- Every new endpoint needs a test proving that a user from org B can't list, read, update or delete org A's data.

## Permissions
- Org-role permissions live in `apps/core/permissions.py`: `IsOrgMember`, `IsOrgEditorOrReadOnly`, `IsOrgAdmin`, `IsOrgAdminOrReadOnly` and `IsOrgOwner`.
  - They read `view.membership`, so they also enforce login and membership. There's no need to add `IsAuthenticated` next to them.
  - Using one on a view without `OrgScopedMixin` raises `ImproperlyConfigured`.
- App-specific object permissions go in that app's `permissions.py`, for example `IsProjectMember` in projects and `CanEditTask` in tasks.
- Viewer is read-only. Member can create and edit tasks in their projects. Admin and Owner manage projects and members. Only Owner can delete the org.
- Outsiders get a 404 from the mixin. Members whose role isn't allowed get a 403 from the permission class.
- Org-specific permissions live in `apps/organizations/permissions.py`:
  - `OrganizationPermission` covers `/api/organizations/{id}/`: any member reads, owner and admin rename, the owner deletes.
  - `MembershipPermission` covers `/api/members/`.
- Membership rules (`/api/members/`):
  - The owner's membership can't be removed or have its role changed.
  - Nobody can be made owner through the API. A role can only be set to admin, member or viewer (`ASSIGNABLE_ROLES`). Ownership transfer will be a separate feature later.
  - Only the owner can change or remove an admin. Admins can manage members and viewers.
  - Any non-owner can leave the org by deleting their own membership. The owner must delete or transfer the org instead.
  - There's no create endpoint for members. People join through invitations.
- Invitation rules:
  - `/api/invitations/` uses `X-Org-ID` and is restricted to owner and admin (`IsOrgAdmin`).
  - The role can't be `owner`, and inviting an existing member returns 400.
  - Re-inviting an email with an open, unaccepted invite (pending or expired) calls `Invitation.refresh()`: new token, new expiry, updated role. That returns 200. A brand-new invite returns 201.
  - The token is never in list or detail responses. Only the create response includes it, inside `accept_url` (`FRONTEND_URL/invitations/accept?token=...`), until invitation emails exist (Step 6).
  - Accepted invites can't be revoked (400).
  - `POST /api/invitations/accept/` takes `{token}` and **no** `X-Org-ID`; it requires login.
    - The logged-in user's email must match the invite, ignoring case. Otherwise it returns 403.
    - Unknown tokens return 404. Used, expired or already-a-member cases return 400.
    - On success it creates the Membership, sets `accepted_at` and returns 201 with `{organization, role}`.
  - This route is declared before the router in `apps/organizations/urls.py`, so `accept` isn't parsed as an id.
  - Status (`pending`/`accepted`/`expired`) is computed (`Invitation.status`) and filterable with `?status=` (`apps/organizations/filters.py`).
- The default is `IsAuthenticated`. Only health and auth endpoints are public.

## API conventions
- Use ViewSets with routers, under the `/api/` prefix, with plural nouns. Nest tasks under projects: `/api/projects/{id}/tasks/`.
- Non-CRUD actions use `@action`, for example `PATCH /api/tasks/{id}/move/` for status plus position.
- Use django-filter `FilterSet` classes for filtering, plus `SearchFilter` and `OrderingFilter`. Pagination is page-number with `PAGE_SIZE=20`.
- Use separate read and write serializers when the shapes differ. Return nested summaries, such as assignee name and email, on reads.
- Avoid N+1 queries: use `select_related` and `prefetch_related` in `get_queryset()`.
- Add `@extend_schema` wherever drf-spectacular can't infer the request or response shape. Keep the schema free of warnings. If two choice fields share a name but have different options, name them in `SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]`.
- Errors use DRF's standard format. Don't invent custom error envelopes. When raising field errors by hand, wrap messages in lists, for example `ValidationError({"token": ["..."]})`, so they match DRF's own field errors.

## Celery
- Tasks must be idempotent, take IDs (never model instances), and use `autoretry_for` with backoff for network or email errors.
- Trigger tasks with `transaction.on_commit(lambda: task.delay(id))`.
- Scheduled jobs (due-date reminders, weekly summaries) go in `CELERY_BEAT_SCHEDULE`.

## Testing
- Use pytest and pytest-django with factory_boy factories, in `apps/<app>/tests/factories.py`. Shared fixtures go in the root `conftest.py`.
  - Existing factories: `UserFactory` (accounts), plus `OrganizationFactory`, `MembershipFactory` and `InvitationFactory` (organizations). `OrganizationFactory` also creates the owner's `owner` membership.
  - In test requests, send the org header as `HTTP_X_ORG_ID`.
- Test models, permissions and API endpoints, including tenant isolation. Aim for coverage of at least 85%.
- In tests, Celery runs tasks immediately (`CELERY_TASK_ALWAYS_EAGER`) and email goes to the in-memory `locmem` backend.

## Workflow
- Work in small steps, one feature at a time. Models, then serializers, then views and URLs, then tests.
- Run `pytest` before saying a task is done, and create migrations for any model change.
- Don't commit `.env`, secrets, `db.sqlite3` or `.venv`.
- Write commit messages in the imperative mood and keep each commit to one feature.

## Roadmap
- [x] 0. Project setup: settings, custom User, JWT, Swagger docs, Celery, Pytest
- [x] 1. Organizations, memberships, invitations, org scoping and permissions, tenant-isolation tests
  - [x] Models: Organization, Membership, Invitation (plus admin and migration)
  - [x] Tenant scoping: `OrgScopedMixin` (`apps/core/tenancy.py`) and tests
  - [x] Org-role permissions (`apps/core/permissions.py`) and the role-matrix tests
  - [x] API: organizations at `/api/organizations/` (no header; create, list mine, retrieve, PATCH, delete) and their tests
  - [x] API: members at `/api/members/` (`X-Org-ID`; list, retrieve, PATCH role, delete or leave) and their tests
  - [x] API: invitations at `/api/invitations/` (create, list, retrieve, revoke) and `/api/invitations/accept/` (by token, no header), with tests
- [ ] 2. Registration endpoint, `/api/auth/me/`, list my organizations
- [ ] 3. Projects CRUD with members and archive
- [ ] 4. Tasks CRUD with filters, search, ordering, pagination and the `move` action
- [ ] 5. Comments and the activity log
- [ ] 6. Notifications: Celery emails, a Beat job for due-date reminders, an in-app notifications API
- [ ] 7. Dashboard stats endpoint, coverage of at least 85%, GitHub Actions CI
- [ ] 8. Production: Dockerfile, Gunicorn, Nginx, AWS (EC2 + RDS + ElastiCache), HTTPS
