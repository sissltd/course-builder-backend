from django.db import models


class PaymentProcessors(models.TextChoices):
    """Payment processors available in the platform."""

    FLUTTERWAVE = "FLUTTERWAVE", "Flutterwave"
    PAYSTACK = "PAYSTACK", "Paystack"


class KYCProvider(models.TextChoices):
    """KYC service providers available in the platform."""

    #We'll keep SISSL out of the options until it becomes functional
    # SISSL = "SISSL", "SISSL"
    YOUVERIFY = "YOUVERIFY", "YouVerify"
