from django.core.management.base import BaseCommand

from api.reviews.services.review_sla_service import run_sla_sweep


class Command(BaseCommand):
    """Alert admins about overdue reviews and flag stalled courses.

    Scheduled every five minutes via CELERY_BEAT_SCHEDULE; also runnable by
    hand to check what the sweep would report without waiting for the next
    tick.
    """

    help = "Run the review SLA sweep once."

    def handle(self, *args, **options):
        report = run_sla_sweep()
        self.stdout.write(
            self.style.SUCCESS(
                "review sla sweep: "
                + " ".join(f"{key}={value}" for key, value in report.items())
            )
        )
