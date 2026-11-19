"""Load the Pixel Forge Studio demo workspace used in the walkthrough video.

    python manage.py seed_demo           # into an empty database
    python manage.py seed_demo --reset   # wipe all data first, then load

Every demo account uses the password below.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models.signals import post_save

from apps.accounts.models import User
from apps.assistant import signals as assistant_signals
from apps.projects.models import Project
from apps.tasks.models import Comment, Task

DEMO_PASSWORD = "DemoPass!2026"
DEMO_USERS = [
    ("priya@pixelforge.dev", "Owner"),
    ("arjun@pixelforge.dev", "Admin"),
    ("neha@pixelforge.dev", "Member"),
    ("rohan@pixelforge.dev", "Member"),
    ("kavya@pixelforge.dev", "Viewer"),
]

# The fixture already contains the Ask TaskHub knowledge chunks, so don't
# queue re-indexing jobs for every project, task and comment while loading.
INDEX_RECEIVERS = [
    (assistant_signals.index_project, Project),
    (assistant_signals.index_task, Task),
    (assistant_signals.index_comment, Comment),
]


class Command(BaseCommand):
    help = "Load the Pixel Forge Studio demo data (users, orgs, projects, tasks, comments, notifications)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete ALL existing data in the database before loading the demo data.",
        )

    def handle(self, *args, reset=False, **options):
        if reset:
            call_command("flush", interactive=False, verbosity=0)
        elif User.objects.exists():
            raise CommandError(
                "The database already has users. Run with --reset to wipe it and load the demo data."
            )

        for receiver, sender in INDEX_RECEIVERS:
            post_save.disconnect(receiver, sender=sender)
        try:
            call_command("loaddata", "demo_data", verbosity=0)
        finally:
            for receiver, sender in INDEX_RECEIVERS:
                post_save.connect(receiver, sender=sender)

        self.stdout.write(self.style.SUCCESS("Demo data loaded: Pixel Forge Studio + Weekend Labs."))
        self.stdout.write(f"Sign in with any of these (password: {DEMO_PASSWORD}):")
        for email, role in DEMO_USERS:
            self.stdout.write(f"  {email:<24} {role}")
