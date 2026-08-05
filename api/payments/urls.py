from django.urls import path

from api.payments.views.bankaccount_views import (
    BankAccountDetailView,
    BankAccountListCreateView,
    BankAccountSetDefaultView,
    BankAccountSuspendView,
    BankListView,
    VerifyBankAccountView,
)

urlpatterns = [
    path("bank-accounts/", BankAccountListCreateView.as_view(), name="bank-accounts-list-create"),
    path("bank-accounts/<uuid:pk>/", BankAccountDetailView.as_view(), name="bank-accounts-detail"),
    path("bank-accounts/<uuid:pk>/default/", BankAccountSetDefaultView.as_view(), name="bank-accounts-set-default"),
    path("bank-accounts/<uuid:pk>/suspend/", BankAccountSuspendView.as_view(), name="bank-accounts-suspend"),
    path("bank-accounts/verify/", VerifyBankAccountView.as_view(), name="bank-accounts-verify"),
    path("banks/", BankListView.as_view(), name="banks-list"),
]
