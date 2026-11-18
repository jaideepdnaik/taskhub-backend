from django.core.management.base import BaseCommand

from apps.assistant.indexing import reindex


class Command(BaseCommand):
    help = "Build or refresh Ask TaskHub's knowledge index (unchanged text isn't re-embedded)."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, help="Only index this organization id.")

    def handle(self, *args, **options):
        counts = reindex(organization_id=options["org"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Indexed {counts['project']} projects and {counts['task']} tasks "
                "(with their comments)."
            )
        )
