"""What a creator is paid for a course, and when.

How much: each category (or a topic, overriding it) carries three creator
prices, one per CreatorPriceType. A course locks all three when it is
submitted (`Course.creator_rates_snapshot`), and is paid the one of its
type:

* AI_BUILT - the course was built with our AI;
* WITHOUT_VIDEO - the creator wrote it and left the video to the
  Production Engine;
* WITH_VIDEO - the creator (or a developer) supplies the video.

When: straight into the creator's wallet, in the same transaction as the
approval that earns it -

* the video is the engine's: when Second Review approves it and it passes to
  the Verifier;
* otherwise (the creator's or the developer's own video): when QA approves it.

Only course creators are paid, and a developer's course only when the
developer's plan pays (not BYPASS_ACCOUNT, and not a submission marked
payout_bypass under BYPASS_PER_SUBMISSION). A course is paid once.
"""

import logging
from decimal import Decimal

from django.utils import timezone

from api.authentication.services.activity_service import log_activity
from api.catalog.enums import CreatorPriceType
from api.courses.enums import CourseSourceType, VideoProvider
from api.courses.models import Course
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.reviews.enums import ReviewStage
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.wallet.services import wallet_service

logger = logging.getLogger(__name__)

COURSE_PAYMENT_ACCOUNT = "course_payment"
"""The internal ledger account course payouts are drawn from (payments/0010)."""

#: The seats a course passes after its text is approved, in order. A course
#: is paid at its payout seat, or at a later one if it was not paid there
#: (e.g. it was already past that seat when these rules shipped).
PAYOUT_SEATS = (ReviewStage.SECOND_REVIEW, ReviewStage.VERIFICATION, ReviewStage.QA)


def reference_for(course: Course) -> str:
    return f"course_payment_{course.id}"


# --- how much -------------------------------------------------------------------------


def price_type_for(course: Course) -> str:
    """The CreatorPriceType a course is paid at, from how it was made."""

    if course.source_type == CourseSourceType.AI_GENERATED:
        return CreatorPriceType.AI_BUILT
    if course.video_provider == VideoProvider.PRODUCTION_ENGINE:
        return CreatorPriceType.WITHOUT_VIDEO
    return CreatorPriceType.WITH_VIDEO


def current_rates(course: Course) -> dict[str, str]:
    """The course's topic (or category) prices now, by type."""

    priced_by = course.topic if course.topic_id else course.category
    return {price_type: str(priced_by.price_for(price_type)) for price_type in CreatorPriceType.values}


def lock_rates(course: Course) -> None:
    """At submission: lock the three prices and set the expected payout.
    Sets fields only; the caller saves `creator_rates_snapshot`,
    `payout_type` and `creator_price_snapshot`."""

    course.creator_rates_snapshot = current_rates(course)
    refresh_expected_payout(course)


def refresh_expected_payout(course: Course) -> None:
    """Re-pick the expected payout from the locked prices, after something
    that decides the type changed (who makes the video). Sets
    `payout_type` and `creator_price_snapshot`; the caller saves them."""

    course.payout_type = price_type_for(course)
    locked = (course.creator_rates_snapshot or {}).get(course.payout_type)
    course.creator_price_snapshot = Decimal(locked) if locked is not None else None


# --- when ---------------------------------------------------------------------------------


def payout_seat(course: Course) -> str:
    """Where the course is paid: Second Review when the engine makes its
    video, QA otherwise."""

    if course.video_provider == VideoProvider.PRODUCTION_ENGINE:
        return ReviewStage.SECOND_REVIEW
    return ReviewStage.QA


def is_payable(course: Course) -> bool:
    """Creators are paid; a developer only when their plan pays for this
    submission."""

    if course.source_type != CourseSourceType.DEVELOPER_API:
        return True
    from api.mie.enums import MiePlanType
    from api.mie.models import CourseSubmission

    submission = CourseSubmission.objects.select_related("developer").filter(resulting_course=course).first()
    if submission is None:
        return False
    plan = submission.developer.plan_type
    if plan == MiePlanType.BYPASS_ACCOUNT:
        return False
    if plan == MiePlanType.BYPASS_PER_SUBMISSION and submission.payout_bypass:
        return False
    return True


def pay_on_approval(*, course: Course, seat: str) -> bool:
    """Called by the reviewing service when `seat` approves `course`, inside
    its transaction with the course row locked. Credits the creator's wallet
    if this is the course's payout seat (or past it, unpaid). Returns
    whether a payment was made."""

    if seat not in PAYOUT_SEATS or PAYOUT_SEATS.index(seat) < PAYOUT_SEATS.index(payout_seat(course)):
        return False
    if course.creator_paid_at is not None or not is_payable(course):
        return False
    amount = course.creator_price_snapshot
    if not amount or amount <= 0:
        logger.info("course %s has no creator price to pay", course.id)
        return False

    from api.payments.services.transaction_services import internal_transfer

    internal_transfer(
        amount=amount,
        from_ledger=InternalAccount.objects.get(code_name=COURSE_PAYMENT_ACCOUNT),
        to_ledger=wallet_service.get_or_create_wallet(user=course.creator),
        reference=reference_for(course),
        description=f"Course '{course.title}' approved ({CreatorPriceType(course.payout_type).label})",
        course_id=course.id,
    )
    course.creator_paid_at = timezone.now()
    course.save(update_fields=["creator_paid_at", "updated_datetime"])
    Notification.emit_in_app_notification(
        receivers=[course.creator],
        title="Payment received",
        content=f"{amount} has been added to your wallet for '{course.title}'.",
        metadata={"course_id": course.id, "amount": str(amount), "payout_type": course.payout_type},
    )
    log_activity(
        user=course.creator,
        category=UserActivityCategoryEnums.WALLET,
        action=UserActivityActionEnums.COURSE_PAYOUT_CREDITED,
        summary=f"You were paid {amount} for '{course.title}'."[:255],
        details={"amount": str(amount), "payout_type": course.payout_type, "seat": seat},
        target=course,
    )
    return True
