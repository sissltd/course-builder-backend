import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from api.wallets.models.bankaccount_models import BankAccount
from shared.services.paystack_service import PaystackService
from shared.utils.encryption import decrypt_field

logger = logging.getLogger(__name__)

@receiver(post_save, sender=BankAccount)
def generate_paystack_recipient_code(sender, instance, **kwargs):
    try:
        payload = {
            "account_number": decrypt_field(instance.account_number),
            "bank_code": instance.bank_code,
            "name": instance.account_name,
        }

        successful, resp = PaystackService.create_transfer_recipient(**payload)
    except Exception as exc:
        logger.warning(f"Error verifying account and creating customer code: {exc}")
    else:
        if successful and not instance.paystack_recipient_code:
            instance.paystack_recipient_code = resp.get("recipient_code")
            instance.save()
