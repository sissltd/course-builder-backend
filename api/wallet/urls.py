from django.urls import path

from api.wallet import views as wallet_views

urlpatterns = [
    path("wallet/", wallet_views.WalletDetailView.as_view(), name="wallet-detail"),
    path(
        "payout-accounts/",
        wallet_views.PayoutAccountListCreateView.as_view(),
        name="wallet-payout-accounts",
    ),
    path(
        "payout-accounts/<uuid:pk>/",
        wallet_views.PayoutAccountDestroyView.as_view(),
        name="wallet-payout-account-detail",
    ),
    path(
        "withdrawals/",
        wallet_views.WithdrawalRequestCreateView.as_view(),
        name="wallet-withdrawals",
    ),
    path(
        "withdrawals/<uuid:withdrawal_request_id>/confirm/",
        wallet_views.WithdrawalConfirmView.as_view(),
        name="wallet-withdrawal-confirm",
    ),
]
