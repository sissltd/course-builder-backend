from django.core.management.base import BaseCommand

from api.mie.services.webhook_dispatcher import dispatch_due_events


class Command(BaseCommand):
    """Deliver all due MIE webhook events in one O(N) pass.

    Schedule this as a single periodic job (cron every minute, or a
    celery beat entry) - it self-paces: no due events means one cheap
    indexed SELECT and an immediate exit.
    """

    help = "Dispatch due MIE webhook events to developer endpoints."

    def handle(self, *args, **options):
        report = dispatch_due_events()
        self.stdout.write(self.style.SUCCESS(f"mie webhooks: {report}"))
