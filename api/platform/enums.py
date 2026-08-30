from django.db import models


class PaymentProcessors(models.TextChoices):
    """Payment processors available in the platform."""

    FLUTTERWAVE = "FLUTTERWAVE", "Flutterwave"
    PAYSTACK = "PAYSTACK", "Paystack"
