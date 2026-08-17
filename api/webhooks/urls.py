from django.urls import path

from api.webhooks.views.flutterwave_webhook_views import FlutterwaveWebhookView
from api.webhooks.views.paystack_webhook_views import PaystackWebhookView

app_name = "webhook"

urlpatterns = [
    path("webhooks/paystack/", PaystackWebhookView.as_view(), name="paystack-webhook"),
    path("webhooks/flutterwave/", FlutterwaveWebhookView.as_view(), name="flutterwave-webhook"),
]
