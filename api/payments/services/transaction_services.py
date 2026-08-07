from django.contrib.contenttypes.models import ContentType
from django.db.models import QuerySet

from api.payments.models.transaction_model import Transaction
from api.users.models import User
from api.wallet.models import Wallet


def list_transactions(*, user: User) -> QuerySet[Transaction]:
    """Return the transaction history for `user`'s wallet, newest first."""

    wallet_ct = ContentType.objects.get_for_model(Wallet)

    try:
        wallet_id = Wallet.objects.values_list('id', flat=True).get(user=user)
        
        queryset = Transaction.objects.filter(
            wallet_type=wallet_ct,
            wallet_id=wallet_id
        )
    except Wallet.DoesNotExist:
        # Handle users without a wallet
        queryset = Transaction.objects.none()

    return queryset
