from django.db import models


class PaymentProcessors(models.TextChoices):
    """Payment processors available in the platform."""

    FLUTTERWAVE = "FLUTTERWAVE", "Flutterwave"
    PAYSTACK = "PAYSTACK", "Paystack"


class KYCProvider(models.TextChoices):
    """KYC service providers available in the platform."""

    SISSL = "SISSL", "SISSL"
    YOUVERIFY = "YOUVERIFY", "YouVerify"
