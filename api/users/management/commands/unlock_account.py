from django.core.management.base import BaseCommand, CommandError

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums
from api.users.models import User


class Command(BaseCommand):
    """Break-glass unlock for an account locked out by failed logins.

    Deliberately a management command rather than an API endpoint. The Super
    Admin seat cannot be moderated through any route - staff_service and
    user_admin_service both refuse it - so an endpoint that could clear its
    lockout would be the one privileged-account write the API otherwise does
    not expose, and would need protecting accordingly. Requiring shell access
    puts this at the same trust level as setting SUPERADMIN_BOOTSTRAP_TOKEN,
    which is where operations of this kind already live.

    A lockout expires on its own after the window in
    api.authentication.serializers.login_serializer, so this is for when
    waiting is not acceptable - typically the platform owner locked out of
    their own deployment.
    """

    help = "Clear the login lockout on an account, identified by email."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email address of the locked account.")

    def handle(self, *args, **options):
        email = options["email"]

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No account found for '{email}'.")

        if not user.locked_until and not user.failed_login_attempts:
            self.stdout.write(f"{user.email} is not locked; nothing to do.")
            return

        was_locked_until = user.locked_until
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])

        # Logged against the unlocked account rather than an actor: whoever ran
        # this had shell access, so there is no authenticated user to attribute
        # it to, and the account's own history is where an unexplained unlock
        # needs to be visible.
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.LOCKOUT_CLEARED,
            summary="Login lockout cleared via the unlock_account command.",
            details={"previous_locked_until": str(was_locked_until)},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Cleared the login lockout on {user.email}. "
                "They can sign in immediately."
            )
        )
