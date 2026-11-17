"""Notification triggers in the API, and the Celery jobs.

In tests Celery runs tasks eagerly and email goes to `mail.outbox`. Jobs are
queued with transaction.on_commit, so requests that should enqueue one run
inside `django_capture_on_commit_callbacks(execute=True)`.
"""
from datetime import timedelta
from smtplib import SMTPServerDisconnected
from unittest import mock

import pytest
from celery.exceptions import Retry
from django.core import mail
from django.utils import timezone

from apps.notifications.models import Notification, NotificationType
from apps.notifications.tasks import (
    send_due_date_reminders,
    send_invitation_email,
    send_notification_email,
)
from apps.notifications.tests.factories import NotificationFactory
from apps.organizations.models import Role
from apps.organizations.tests.factories import (
    InvitationFactory,
    MembershipFactory,
    OrganizationFactory,
)
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import TaskStatus
from apps.tasks.tests.factories import TaskFactory


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def member(org):
    return MembershipFactory(organization=org, role=Role.MEMBER).user


@pytest.fixture
def project(org, member):
    return ProjectFactory(organization=org, members=[org.owner, member])


@pytest.fixture
def owner_client(api_client, org):
    api_client.force_authenticate(user=org.owner)
    api_client.credentials(HTTP_X_ORG_ID=str(org.id))
    return api_client


@pytest.mark.django_db
class TestTaskAssigned:
    def test_assign_on_create_notifies_and_emails(
        self, owner_client, org, member, project, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = owner_client.post(
                f"/api/projects/{project.id}/tasks/", {"title": "Fix login", "assignee_id": member.id}
            )

        assert response.status_code == 201
        notification = Notification.objects.get(recipient=member)
        assert notification.type == NotificationType.TASK_ASSIGNED
        assert notification.organization == org
        assert notification.payload["task_title"] == "Fix login"
        assert notification.payload["actor"]["email"] == org.owner.email
        assert notification.emailed_at is not None

        [email] = mail.outbox
        assert email.to == [member.email]
        assert "Fix login" in email.subject
        assert f"/projects/{project.id}/tasks/{response.json()['id']}" in email.body

    def test_reassign_via_patch_notifies_new_assignee(
        self, owner_client, member, project, django_capture_on_commit_callbacks
    ):
        task = TaskFactory(project=project)
        with django_capture_on_commit_callbacks(execute=True):
            owner_client.patch(f"/api/tasks/{task.id}/", {"assignee_id": member.id})
        assert Notification.objects.filter(recipient=member).count() == 1
        assert len(mail.outbox) == 1

    def test_self_assignment_is_silent(
        self, owner_client, org, project, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            owner_client.post(
                f"/api/projects/{project.id}/tasks/", {"title": "Mine", "assignee_id": org.owner.id}
            )
        assert not Notification.objects.exists()
        assert mail.outbox == []

    def test_other_edits_and_unassign_are_silent(
        self, owner_client, member, project, django_capture_on_commit_callbacks
    ):
        task = TaskFactory(project=project, assignee=member)
        with django_capture_on_commit_callbacks(execute=True):
            owner_client.patch(f"/api/tasks/{task.id}/", {"title": "Renamed"})
            owner_client.patch(f"/api/tasks/{task.id}/", {"assignee_id": None}, format="json")
        assert not Notification.objects.exists()

    def test_failed_request_sends_nothing(
        self, owner_client, member, project, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = owner_client.post(
                f"/api/projects/{project.id}/tasks/",
                {"title": "X", "assignee_id": member.id, "priority": "meh"},
            )
        assert response.status_code == 400
        assert not Notification.objects.exists()
        assert mail.outbox == []


@pytest.mark.django_db
class TestCommentAdded:
    def test_notifies_assignee_and_reporter_but_not_author(self, api_client, org, member, project):
        reporter = MembershipFactory(organization=org, role=Role.MEMBER).user
        project.members.add(reporter)
        task = TaskFactory(project=project, assignee=member, reporter=reporter)
        api_client.force_authenticate(user=reporter)
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))

        api_client.post(f"/api/tasks/{task.id}/comments/", {"body": "Can you check this?"})

        [notification] = Notification.objects.all()
        assert notification.recipient == member
        assert notification.type == NotificationType.COMMENT_ADDED
        assert notification.payload["excerpt"] == "Can you check this?"
        assert notification.payload["actor"]["email"] == reporter.email
        assert mail.outbox == []  # in-app only

    def test_third_party_comment_notifies_both(self, owner_client, org, member, project):
        reporter = MembershipFactory(organization=org, role=Role.MEMBER).user
        task = TaskFactory(project=project, assignee=member, reporter=reporter)
        owner_client.post(f"/api/tasks/{task.id}/comments/", {"body": "hi"})
        assert set(Notification.objects.values_list("recipient", flat=True)) == {member.id, reporter.id}

    def test_reporter_who_left_org_is_not_notified(self, owner_client, org, member, project):
        departed = MembershipFactory(organization=org, role=Role.MEMBER)
        task = TaskFactory(project=project, reporter=departed.user)
        departed.delete()
        owner_client.post(f"/api/tasks/{task.id}/comments/", {"body": "hi"})
        assert not Notification.objects.exists()


@pytest.mark.django_db
class TestInvitationEmail:
    def test_invite_sends_email_with_link(self, owner_client, org, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = owner_client.post("/api/invitations/", {"email": "riya@example.com"})

        [email] = mail.outbox
        assert email.to == ["riya@example.com"]
        assert org.name in email.subject
        token = response.json()["accept_url"].split("token=")[1]
        assert f"/invitations/accept?token={token}" in email.body

    def test_reinvite_resends_with_new_token(self, owner_client, org, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            owner_client.post("/api/invitations/", {"email": "riya@example.com"})
            response = owner_client.post("/api/invitations/", {"email": "riya@example.com"})
        assert len(mail.outbox) == 2
        new_token = response.json()["accept_url"].split("token=")[1]
        assert new_token in mail.outbox[1].body

    def test_skips_if_no_longer_pending(self):
        invitation = InvitationFactory(accepted_at=timezone.now())
        assert send_invitation_email(invitation.id) == "skipped"
        assert send_invitation_email(999999) == "skipped"
        assert mail.outbox == []


@pytest.mark.django_db
class TestSendNotificationEmail:
    def test_is_idempotent(self):
        notification = NotificationFactory()
        assert send_notification_email(notification.id) == "sent"
        assert send_notification_email(notification.id) == "skipped"
        assert len(mail.outbox) == 1

    def test_missing_notification_is_skipped(self):
        assert send_notification_email(999999) == "skipped"

    def test_retries_on_mail_errors(self):
        notification = NotificationFactory()
        with mock.patch(
            "apps.notifications.tasks.send_mail", side_effect=SMTPServerDisconnected("down")
        ):
            # autoretry_for turns the SMTP error into a Retry (re-queued with back-off).
            with pytest.raises(Retry):
                send_notification_email.delay(notification.id)
        notification.refresh_from_db()
        assert notification.emailed_at is None  # will be sent on a later attempt

    def test_retry_policy(self):
        assert send_notification_email.autoretry_for
        assert send_notification_email.retry_backoff is True
        assert send_notification_email.max_retries == 5


@pytest.mark.django_db
class TestDueDateReminders:
    @pytest.fixture
    def tomorrow(self):
        return timezone.localdate() + timedelta(days=1)

    def test_reminds_assignees_of_open_tasks_due_tomorrow(
        self, org, member, project, tomorrow, django_capture_on_commit_callbacks
    ):
        due = TaskFactory(project=project, assignee=member, due_date=tomorrow, title="Ship it")
        TaskFactory(project=project, assignee=member, due_date=tomorrow, status=TaskStatus.DONE)
        TaskFactory(project=project, assignee=member, due_date=tomorrow + timedelta(days=1))
        TaskFactory(project=project, due_date=tomorrow)  # unassigned
        archived = ProjectFactory(organization=org, members=[member], is_archived=True)
        TaskFactory(project=archived, assignee=member, due_date=tomorrow)

        with django_capture_on_commit_callbacks(execute=True):
            assert send_due_date_reminders() == 1

        [notification] = Notification.objects.all()
        assert notification.recipient == member
        assert notification.type == NotificationType.TASK_DUE_SOON
        assert notification.payload["task_id"] == due.id
        assert notification.payload["due_date"] == tomorrow.isoformat()
        [email] = mail.outbox
        assert email.to == [member.email]
        assert "Ship it" in email.subject

    def test_running_twice_does_not_duplicate(
        self, member, project, tomorrow, django_capture_on_commit_callbacks
    ):
        TaskFactory(project=project, assignee=member, due_date=tomorrow)
        with django_capture_on_commit_callbacks(execute=True):
            assert send_due_date_reminders() == 1
            assert send_due_date_reminders() == 0
        assert Notification.objects.count() == 1
        assert len(mail.outbox) == 1

    def test_is_scheduled_daily(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE["send-due-date-reminders"]
        assert entry["task"] == "apps.notifications.tasks.send_due_date_reminders"
        assert entry["schedule"].hour == {8} and entry["schedule"].minute == {0}
