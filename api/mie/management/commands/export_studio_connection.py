"""Reissue one test account and write its credentials to a private local file."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.exceptions import APIException

from api.mie.models import DeveloperAccount
from api.mie.services.developer_service import rotate_developer_credentials
from api.users.models import User


class Command(BaseCommand):
    help = "Rotate an explicitly selected approved MIE account and export a private Studio connection bundle."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", required=True)
        parser.add_argument("--actor-email", required=True)
        parser.add_argument("--api-base-url", required=True)
        parser.add_argument("--studio-origin", required=True)
        parser.add_argument("--environment", choices=["demo", "live"], required=True)
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        base = options["api_base_url"].rstrip("/")
        origin = options["studio_origin"].rstrip("/")
        for url in (base, origin):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise CommandError(
                    "Use HTTPS URLs without credentials, queries or fragments."
                )
        if urlsplit(origin).path:
            raise CommandError("Studio origin must not contain a path.")
        callback = origin + "/studio/api/webhooks/soludesk/" + options["environment"]
        account = DeveloperAccount.objects.filter(pk=options["account_id"]).first()
        actor = User.objects.filter(
            email__iexact=options["actor_email"], is_active=True
        ).first()
        if account is None or actor is None:
            raise CommandError(
                "Selected account or active administrator was not found."
            )
        if not account.webhook_endpoints.filter(
            url=callback, is_deleted=False
        ).exists():
            raise CommandError(
                "Register this account's exact Studio callback before export: "
                + callback
            )
        output = Path(options["output"]).expanduser().absolute()
        try:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise CommandError(
                "Output already exists; choose a new private filename."
            ) from exc
        try:
            with os.fdopen(fd, "w") as stream, transaction.atomic():
                key = rotate_developer_credentials(actor=actor, account=account)
                account.refresh_from_db()
                json.dump(
                    {
                        "schema_version": "studio.connection.v1",
                        "environment": options["environment"],
                        "account_id": str(account.id),
                        "api_base_url": base,
                        "api_key": key,
                        "signing_secret": account.signing_secret,
                        "webhook_url": callback,
                    },
                    stream,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except Exception as exc:
            output.unlink(missing_ok=True)
            if isinstance(exc, APIException):
                raise CommandError(
                    "Credential export denied by the account permission/status checks."
                ) from exc
            raise
        self.stdout.write(
            "Private connection bundle written (mode 0600): " + str(output)
        )
