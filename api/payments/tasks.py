import logging

from celery import shared_task
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.wallet.services import wallet_service

logger = logging.getLogger(__name__)

COURSE_PAYMENT_INTERNAL_ACCOUNT_CODENAME = "course_payment"


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="courses.release_course_payment",
)
def release_course_payment(self, *args):
    """Schedule the release of course payment to the course creator, after the course is approved
    """
    course_id = args[0]
    try:
        course = Course.objects.get(id=course_id)
    except Course.DoesNotExist as exc:
        logger.error(f"[courses.release_course_payment] Course not found: {exc}")
        return
    
    if course.status != CourseStatus.APPROVED: #Just doing a second check
        logger.error(f"[courses.release_course_payment] Course not approved: {course_id}")
        return

    reference_str = f"course_payment_{course.id}"
    if Transaction.objects.filter(reference=reference_str).exists():
        logger.warning(f"[courses.release_course_payment] Payout already processed for reference: {reference_str}")
        return

    try:
        debit_wallet = InternalAccount.objects.get(code_name=COURSE_PAYMENT_INTERNAL_ACCOUNT_CODENAME)
        credit_wallet = wallet_service.get_or_create_wallet(user=course.creator)
        from api.payments.services.transaction_services import internal_transfer
        with transaction.atomic():
            # Double-check existence inside the transaction lock to prevent race conditions
            if Transaction.objects.filter(reference=reference_str).exists():
                return  # Exit if the transaction was created by another concurrent process
            internal_transfer(
            amount=course.creator_price_snapshot,
            from_ledger=debit_wallet,
            to_ledger=credit_wallet,
            reference=reference_str,
            description=f"Course '{course.title}' approved after QA verification",
            course_id=course.id,
        )
    except (InternalAccount.DoesNotExist, ObjectDoesNotExist) as exc:
        # DO NOT retry if internal accounts are missing; this requires developer intervention.
        logger.critical(f"[courses.release_course_payment] Configuration Error: {exc}")
        raise
    except Exception as exc:
        # Retry on temporary infrastructure/database connection drops
        logger.error(f"[courses.release_course_payment] Failed: {exc}")
        raise self.retry(exc=exc)

    try:
        Notification.emit_in_app_notification(
            receivers=[course.creator],
            title="Course approved",
            content=f"Your course '{course.title}' passed QA verification and has been approved.",
            metadata={
                "course_id": course.id,
                "amount": course.creator_price_snapshot,
            },
        )
    except Exception as exc:
        logger.error(f"[courses.release_course_payment] Failed to send notification: {exc}")
