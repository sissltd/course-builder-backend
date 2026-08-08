from django.urls import path

from api.wallet import views as wallet_views

urlpatterns = [
    path("wallet/", wallet_views.WalletDetailView.as_view(), name="wallet-detail"),
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
