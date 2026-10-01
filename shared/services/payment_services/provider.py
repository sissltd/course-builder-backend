
from api.platform.enums import PaymentProcessors
from api.platform.services.platform_settings_service import get_settings

from . import FlutterwaveService


def get_payment_provider():
    settings = get_settings()
    payment_processor = settings.payment_processor
    
    #This dict grows as more providers are implemented
    processor_dict = {
        PaymentProcessors.FLUTTERWAVE: FlutterwaveService(),
        # PaymentProcessors.PAYSTACK: PaystackService(),
    }

    try:
        return processor_dict[payment_processor]
    except KeyError:
        raise ValueError(f"Unsupported payment processor: {payment_processor}")