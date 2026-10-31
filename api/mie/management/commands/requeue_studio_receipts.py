from django.core.management.base import BaseCommand

from api.mie.models import StudioDeliveryReceipt
from api.mie.services.studio_receipt_sender import requeue


class Command(BaseCommand):
    """Send HELD Studio receipts again once the cause is fixed (a rotated
    secret, a network peer, a reconciled delivery). Each keeps its receipt id
    and payload, so Studio sees a retry, not a new receipt."""

    help = "Requeue held studio.receipt.v1 callbacks."

    def add_arguments(self, parser):
        parser.add_argument(
            "receipt_ids",
            nargs="*",
            help="Receipt ids to requeue. Omit to requeue every held receipt.",
        )

    def handle(self, *args, receipt_ids, **options):
        receipts = StudioDeliveryReceipt.objects.all()
        if receipt_ids:
            receipts = receipts.filter(id__in=receipt_ids)
        count = requeue(receipts)
        self.stdout.write(self.style.SUCCESS(f"studio receipts requeued: {count}"))
