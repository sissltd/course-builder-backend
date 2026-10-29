from django.core.management.base import BaseCommand, CommandError
from rest_framework import exceptions

from api.mie.serializers.developer_admin_serializer import DeveloperRegisterSerializer
from api.mie.services import developer_service
from api.users.models import User


class Command(BaseCommand):
    """Provision a platform-owned SYSTEM developer account (the crawler).

    A command rather than an endpoint on purpose: the raw API key is
    printed once, to the operator's terminal, and never travels through
    an HTTP response, a browser session or anything that logs request
    bodies. Safe to re-run - an existing system account is reported and
    left exactly as it is, and no second key is ever issued.
    """

    help = (
        "Create and approve the MIE system developer account (the crawler) "
        "and print its API key once."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Identity of the system account, e.g. crawler@soludesks.com.",
        )
        parser.add_argument(
            "--webhook-url",
            required=True,
            help=(
                "Public URL of the crawler's webhook receiver. Needs a real "
                "domain - container hostnames fail URL validation."
            ),
        )
        parser.add_argument(
            "--actor-email",
            required=True,
            help="Super Admin the provisioning is carried out and audited as.",
        )

    def handle(self, *args, **options):
        # The same field rules the registration endpoints apply.
        serializer = DeveloperRegisterSerializer(
            data={"email": options["email"], "webhook_url": options["webhook_url"]}
        )
        if not serializer.is_valid():
            raise CommandError(_describe(serializer.errors))

        actor = User.objects.filter(email__iexact=options["actor_email"]).first()
        if actor is None:
            raise CommandError(f"No user has the email {options['actor_email']}.")

        try:
            account, raw_key = developer_service.provision_system_account(
                actor=actor,
                email=serializer.validated_data["email"],
                webhook_url=serializer.validated_data["webhook_url"],
            )
        except (exceptions.PermissionDenied, exceptions.ValidationError) as exc:
            raise CommandError(_describe(exc.detail)) from exc

        if raw_key is None:
            self.stdout.write(
                self.style.WARNING(
                    f"{account.email} is already a system account (status "
                    f"{account.status}); nothing changed and no key was issued. "
                    "A suspended account is restored by approving it, not by "
                    "re-provisioning."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"{account.email} provisioned as an MIE system account.")
        )
        self.stdout.write(
            "API key - shown this once and never again; put it in the "
            "crawler's secret store now:"
        )
        self.stdout.write(raw_key)


def _describe(detail) -> str:
    """Flatten DRF error detail (dict, list or string) into one line."""

    if isinstance(detail, dict):
        return " ".join(f"{field}: {_describe(value)}" for field, value in detail.items())
    if isinstance(detail, list):
        return " ".join(_describe(item) for item in detail)
    return str(detail)
