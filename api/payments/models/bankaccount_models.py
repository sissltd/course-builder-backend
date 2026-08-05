
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from core.mixins import DateHistoryModelMixin, SoftDeleteModelMixin, UUIDPrimaryKeyModelMixin

User = get_user_model()


class BankAccount(
    UUIDPrimaryKeyModelMixin,
    DateHistoryModelMixin,
    SoftDeleteModelMixin
):
    """A model representing a bank account for a user. Extracted from the wallet module for ease of extension and reusability"""

    class BankAccountType(models.TextChoices):
        """How a creator's payout account receives funds."""

        LOCAL = "Local Account", "Local Account"
        MOBILE_MONEY = "Mobile Money", "Mobile Money"

    user = models.ForeignKey(User, related_name="bank_accounts", on_delete=models.PROTECT)
    bank_name = models.CharField(max_length=255)
    account_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=20)
    is_suspended = models.BooleanField(default=False)
    bank_code = models.CharField(max_length=64, blank=True)
    paystack_recipient_code = models.CharField(max_length=50, null=True, blank=True)
    account_type = models.CharField(
        verbose_name=_("Account Type"),
        max_length=15,
        choices=BankAccountType.choices,
        default=BankAccountType.LOCAL,
        help_text=_("Whether this is a local bank account or a mobile money account."),
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "bank_accounts"
        verbose_name = _("Bank Account")
        verbose_name_plural = _("Bank Accounts")

    def __str__(self):
        return f"{self.account_name} - {self.bank_name}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            # A user can only have  one default account at a time
            if self.is_default:
                BankAccount.objects.select_for_update().filter(user=self.user).exclude(id=self.id).update(is_default=False)
