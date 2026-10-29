"""Copy wallet.PayoutAccount -> payments.BankAccount and wallet.Transaction ->
payments.Transaction, preserving primary keys, before wallet.0005 repoints
WithdrawalRequest.payout_account/transaction at the new payments-app models
and drops the old wallet.Transaction table.

Without this step, every existing WithdrawalRequest row's payout_account_id/
transaction_id would violate the new foreign key constraints, since
payments.BankAccount/payments.Transaction were created as fresh, empty
tables (CreateModel, not a rename) rather than inherited from the old
wallet-app tables.

Note: wallet.0003 already converted wallet.Transaction.wallet (a direct FK)
into wallet_id/wallet_type via a RemoveField + AddField pair rather than a
RenameField, which drops and recreates that column - any wallet-attribution
on a Transaction row that existed before that migration ran is already gone
by the time this migration runs and cannot be recovered here. Every other
field (amount, status, type, reference, recipient snapshots) is untouched
by 0003 and is copied across intact.
"""

from django.db import migrations

#: wallet.PayoutAccount.account_type values -> payments.BankAccount.account_type
#: values - the two enums use different literal values for the same choices
#: (see api/wallet/enums.py::PayoutAccountType vs
#: api/payments/models/bankaccount_models.py::BankAccount.BankAccountType).
ACCOUNT_TYPE_MAP = {
    "LOCAL": "Local Account",
    "MOBILE_MONEY": "Mobile Money",
}


def copy_wallet_records_to_payments(apps, schema_editor):
    OldPayoutAccount = apps.get_model("wallet", "PayoutAccount")
    OldTransaction = apps.get_model("wallet", "Transaction")
    NewBankAccount = apps.get_model("payments", "BankAccount")
    NewTransaction = apps.get_model("payments", "Transaction")

    # PayoutAccount -> BankAccount first: Transaction.payout_account_id and
    # WithdrawalRequest.payout_account_id both need the copied row to already
    # exist under the same id.
    for old in OldPayoutAccount.objects.all():
        new = NewBankAccount.objects.create(
            id=old.id,
            user_id=old.user_id,
            bank_name=old.provider_name,
            account_name=old.account_name,
            account_number=old.account_number,
            account_type=ACCOUNT_TYPE_MAP.get(old.account_type, "Local Account"),
            is_default=old.is_default,
        )
        # created_datetime/updated_datetime are auto_now_add/auto_now - only
        # a queryset .update() (bypassing that field machinery) can set them
        # to the original historical values instead of "now".
        NewBankAccount.objects.filter(pk=new.pk).update(
            created_datetime=old.created_datetime,
            updated_datetime=old.updated_datetime,
        )

    for old in OldTransaction.objects.all():
        new = NewTransaction.objects.create(
            id=old.id,
            course_id=old.course_id,
            payout_account_id=old.payout_account_id,
            reference=old.reference,
            amount=old.amount,
            fee=old.fee,
            type=old.type,
            status=old.status,
            description=old.description,
            recipient_account_name=old.recipient_account_name,
            recipient_account_number=old.recipient_account_number,
            recipient_provider_name=old.recipient_provider_name,
            wallet_type_id=old.wallet_type_id,
            wallet_id=old.wallet_id,
        )
        NewTransaction.objects.filter(pk=new.pk).update(
            created_datetime=old.created_datetime,
            updated_datetime=old.updated_datetime,
        )


def delete_copied_payments_records(apps, schema_editor):
    """Reverse: remove exactly the rows this migration created, identified by
    still existing in the old wallet tables (both tables are still present
    at this point in the migration history - only wallet.0005 drops them)."""

    OldPayoutAccount = apps.get_model("wallet", "PayoutAccount")
    OldTransaction = apps.get_model("wallet", "Transaction")
    NewBankAccount = apps.get_model("payments", "BankAccount")
    NewTransaction = apps.get_model("payments", "Transaction")

    NewTransaction.objects.filter(
        id__in=OldTransaction.objects.values_list("id", flat=True)
    ).delete()
    NewBankAccount.objects.filter(
        id__in=OldPayoutAccount.objects.values_list("id", flat=True)
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0003_remove_transaction_txn_wallet_dt_idx_and_more"),
        ("payments", "0004_transaction"),
    ]

    operations = [
        migrations.RunPython(
            copy_wallet_records_to_payments, delete_copied_payments_records
        ),
    ]
