from django.core.management.base import BaseCommand

from api.operations.services.probe_service import run_probes


class Command(BaseCommand):
    """Probe registered services and record one health sample each.

    Scheduled every five minutes via CELERY_BEAT_SCHEDULE; also runnable
    by hand during an incident, which costs nothing and is invaluable at
    3am.
    """

    help = "Record a health sample for every probeable service."

    def handle(self, *args, **options):
        report = run_probes()
        self.stdout.write(
            self.style.SUCCESS(
                "health probes: "
                + " ".join(f"{key}={value}" for key, value in report.items())
            )
        )
