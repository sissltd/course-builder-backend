"""Hard stops for platform-owned (SYSTEM) developer accounts.

The crawler submits through the same public API as any external
developer, but it is a bot: a broken one can bury the review queue far
faster than a person ever could. Two guardrails bound it, and neither
ever touches an EXTERNAL account -

* the rolling daily cap, in submission_service.submit_idea, because it
  has to refuse before anything is stored;
* the rejection circuit breaker, here. When admins reject most of what
  the crawler sends, the account is SUSPENDED - the same freeze a
  superadmin applies by hand, so the key and queue history survive,
  pending webhooks are held rather than dropped, and approving the
  account again restores it.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from api.authentication.services.activity_service import log_activity
from api.mie.enums import DeveloperAccountStatus, MieSourceType, SubmissionStatus
from api.mie.models import CourseSubmission, DeveloperAccount
from api.mie.services.developer_service import suspend_developer
from api.notification.models import Notification
from api.notification.services.email_service import send_templated_email
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums, UserRole
from api.users.models import User

logger = logging.getLogger(__name__)

DECIDED_STATUSES = (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED)
ALERT_TITLE = "MIE crawler suspended by the circuit breaker"


def evaluate_rejection_breaker(*, account: DeveloperAccount, actor) -> bool:
    """Suspend a SYSTEM account whose recent rejection rate is too high.

    decide_submission calls this right after rejecting one of the
    account's ideas, inside the same transaction, so the rejection that
    tips the rate is the one that trips the breaker. Returns True if it
    tripped.

    Only admin decisions count: APPROVED/REJECTED rows decided inside the
    window. Dedup short-circuits are automatic and say nothing about idea
    quality. The window also never reaches back past the account's own
    latest decision, so an account a superadmin has just restored starts
    clean instead of re-tripping on the rejections that suspended it.

    `actor` is the superadmin whose rejection tripped it; the suspension
    and its audit row are recorded under them.
    """

    if account.source_type != MieSourceType.SYSTEM:
        return False

    # Lock before reading status: two admins rejecting at the same moment
    # must produce one suspension and one alert, not two.
    account = DeveloperAccount.objects.select_for_update().get(id=account.id)
    if account.status != DeveloperAccountStatus.APPROVED:
        return False

    window_start = timezone.now() - timedelta(days=settings.MIE_BREAKER_WINDOW_DAYS)
    if account.decided_at and account.decided_at > window_start:
        window_start = account.decided_at

    tally = CourseSubmission.objects.filter(
        developer=account, status__in=DECIDED_STATUSES, decided_at__gte=window_start
    ).aggregate(
        decided=Count("id"),
        rejected=Count("id", filter=Q(status=SubmissionStatus.REJECTED)),
    )
    if tally["decided"] < settings.MIE_BREAKER_MIN_DECISIONS:
        return False
    rejection_rate = tally["rejected"] / tally["decided"]
    if rejection_rate < settings.MIE_BREAKER_REJECTION_RATE:
        return False

    suspend_developer(actor=actor, account=account)
    details = {
        "developer_account_id": str(account.id),
        "developer_email": account.email,
        "decided": tally["decided"],
        "rejected": tally["rejected"],
        "rejection_rate": round(rejection_rate, 3),
        "threshold": settings.MIE_BREAKER_REJECTION_RATE,
        "window_days": settings.MIE_BREAKER_WINDOW_DAYS,
    }
    log_activity(
        user=actor,
        category=UserActivityCategoryEnums.ALERT,
        action=UserActivityActionEnums.ACCOUNT_SUSPENDED,
        summary="MIE circuit breaker suspended a system developer account.",
        details=details,
        target=account,
    )
    # After commit only: if the rejection rolls back, so does the
    # suspension, and nobody should be told the crawler was stopped.
    transaction.on_commit(lambda: _alert_in_app(details))
    transaction.on_commit(lambda: _alert_by_email(details))
    return True


def _alert_in_app(details: dict) -> None:
    """In-app alert to every superadmin.

    Runs after commit. A failure is logged and swallowed: the suspension
    already stands, and an alerting problem must not come back as an error
    on the admin's rejection.
    """

    try:
        super_admins = list(User.objects.filter(role=UserRole.SUPER_ADMIN))
        if super_admins:
            Notification.emit_in_app_notification(
                receivers=super_admins,
                title=ALERT_TITLE,
                content=_alert_text(details),
                metadata=details,
            )
    except Exception:  # noqa: BLE001 - see docstring: never fail the decision
        logger.exception(
            "mie circuit breaker in-app alert failed for account %s",
            details["developer_account_id"],
        )


def _alert_by_email(details: dict) -> None:
    """Email counterpart of _alert_in_app, failing the same quiet way."""

    try:
        recipients = list(
            User.objects.filter(role=UserRole.SUPER_ADMIN).values_list("email", flat=True)
        )
        if recipients:
            send_templated_email(
                receivers=recipients,
                subject=ALERT_TITLE,
                template_name="emails/mie_circuit_breaker_tripped",
                context={
                    "developer_email": details["developer_email"],
                    "decided": details["decided"],
                    "rejected": details["rejected"],
                    "rejection_rate": f"{details['rejection_rate']:.0%}",
                    "threshold": f"{details['threshold']:.0%}",
                    "window_days": details["window_days"],
                },
            )
    except Exception:  # noqa: BLE001 - see _alert_in_app
        logger.exception(
            "mie circuit breaker email alert failed for account %s",
            details["developer_account_id"],
        )


def _alert_text(details: dict) -> str:
    return (
        f"{details['developer_email']} was suspended: admins rejected "
        f"{details['rejected']} of its {details['decided']} recently decided "
        f"ideas ({details['rejection_rate']:.0%}; the limit is "
        f"{details['threshold']:.0%}). Its key and queue are kept - approve "
        "the account under MIE Developers to restore it."
    )
