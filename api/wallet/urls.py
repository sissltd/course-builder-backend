from django.urls import path

from api.wallet import views as wallet_views

urlpatterns = [
    path("wallet/", wallet_views.WalletDetailView.as_view(), name="wallet-detail"),
    path("transactions/", wallet_views.TransactionListView.as_view(), name="wallet-transactions"),
    path("withdrawals/", wallet_views.WithdrawalRequestCreateView.as_view(), name="wallet-withdrawals"),
]
