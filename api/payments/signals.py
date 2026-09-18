import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from api.payments.models.bankaccount_models import BankAccount
from api.platform.enums import PaymentProcessors
from api.platform.models import PlatformSettings
from shared.services.flutterwave_service import FlutterwaveService
from shared.utils.encryption import decrypt_field

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BankAccount)
def generate_flutterwave_recipient_code(sender, instance, **kwargs):
    if instance.flutterwave_recipient_code or PlatformSettings.payment_processor != PaymentProcessors.FLUTTERWAVE:
        return
    try:
        payload = {
            "account_number": decrypt_field(instance.account_number),
            "bank_code": instance.bank_code,
            "account_name": instance.account_name,
        }

        resp = FlutterwaveService().get_recipient_id(**payload)
    except Exception as exc:
        logger.warning(f"Error verifying account and creating Flutterwave customer code: {exc}")
    else:
        if resp and not instance.flutterwave_recipient_code:
            instance.flutterwave_recipient_code = resp
            instance.save()
