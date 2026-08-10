from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "api.payments"

    def ready(self):
        import api.payments.signals  # noqa: F401
