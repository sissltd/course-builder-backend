from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import APIException

from api.authorization import codenames
from api.authorization.services import permission_service
from api.platform.services import platform_settings_service
from api.users.models import User


class Command(BaseCommand):
    help = "Set the default for future first submissions, preserving course review snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["legacy", "text-first"], required=True)
        parser.add_argument("--actor-id", required=True)
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **options):
        if len(options["reason"].strip()) < 20:
            raise CommandError("Provide at least 20 characters of activation evidence.")
        try:
            actor = User.objects.get(pk=options["actor_id"], is_active=True)
            permission_service.require_permission(
                actor, codenames.PLATFORM_EDIT_SETTINGS
            )
            row = platform_settings_service.update_settings(
                staged_review_flow_enabled=options["mode"] == "text-first",
                actor=actor,
                reason=options["reason"],
            )
        except (User.DoesNotExist, ValueError, APIException) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Future submissions use {options['mode']}; existing course modes are unchanged. Default={row.staged_review_flow_enabled}"
            )
        )
