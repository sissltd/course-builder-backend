from django.urls import path

from api.payments.views.bankaccount_views import (
    BankAccountDetailView,
    BankAccountListCreateView,
    BankAccountSetDefaultView,
    BankAccountSuspendView,
    BankListView,
    VerifyBankAccountView,
)
from api.payments.views.transaction_views import TransactionListView

urlpatterns = [
    path("payout-accounts/", BankAccountListCreateView.as_view(), name="bank-accounts-list-create"),
    path("payout-accounts/<uuid:pk>/", BankAccountDetailView.as_view(), name="bank-accounts-detail"),
    path("payout-accounts/<uuid:pk>/default/", BankAccountSetDefaultView.as_view(), name="bank-accounts-set-default"),
    path("payout-accounts/<uuid:pk>/suspend/", BankAccountSuspendView.as_view(), name="bank-accounts-suspend"),
    path("payout-accounts/verify/", VerifyBankAccountView.as_view(), name="bank-accounts-verify"),
    path("banks/", BankListView.as_view(), name="banks-list"),
    path("transactions/", TransactionListView.as_view(), name="payout-transactions"),
]
