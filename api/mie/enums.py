from django.db import models


class DeveloperAccountStatus(models.TextChoices):
    """Lifecycle of an external developer account.

    PENDING  -> registered with email + webhook_url, awaiting superadmin
                approval. No API key exists yet; every authenticated
                endpoint rejects the account.
    APPROVED -> API key issued (shown once at approval). Full access.
    REJECTED -> terminal. The registration was denied; a new registration
                with a different email is required.
    SUSPENDED-> temporary. Data and queue are retained but keys are frozen;
                a superadmin can return the account to APPROVED at any time.
    """

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    SUSPENDED = "SUSPENDED", "Suspended"


class MiePlanType(models.TextChoices):
    """How payouts work for a developer's accepted submissions.

    PAID_PER_SUBMISSION   - each approved idea credits the creator wallet.
    BYPASS_PER_SUBMISSION - payout is skipped only for submissions the
                            superadmin individually marks as bypassed.
    BYPASS_ACCOUNT        - nothing from this developer ever pays out;
                            the superadmin applies this when the creator
                            will not be paid by agreement.
    """

    PAID_PER_SUBMISSION = "PAID_PER_SUBMISSION", "Paid Per Submission"
    BYPASS_PER_SUBMISSION = "BYPASS_PER_SUBMISSION", "Bypass Per Submission"
    BYPASS_ACCOUNT = "BYPASS_ACCOUNT", "Account Bypass"


class SubmissionStatus(models.TextChoices):
    """Every state a course idea submission can sit in.

    All states appear in the queue surfaces; dedup short-circuits
    (DUPLICATE_IN_QUEUE, DUPLICATE_EXISTING, PREVIOUSLY_REJECTED) are set
    immediately at ingestion, before any human review. Decisions are not
    terminal: a superadmin can flip APPROVED <-> REJECTED at any time,
    which re-fires the matching webhook event.
    """

    PENDING_REVIEW = "PENDING_REVIEW", "Pending Review"
    DUPLICATE_IN_QUEUE = "DUPLICATE_IN_QUEUE", "Duplicate In Queue"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING", "Duplicate Existing"
    PREVIOUSLY_REJECTED = "PREVIOUSLY_REJECTED", "Previously Rejected"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class WebhookEventType(models.TextChoices):
    """Events pushed to a developer's webhook_url.

    Fired immediately on every transition including automated dedup
    short-circuits - devs never poll; the webhook is the only channel.
    """

    SUBMISSION_QUEUED = "SUBMISSION_QUEUED", "Submission Queued"
    SUBMISSION_DUPLICATE_IN_QUEUE = (
        "SUBMISSION_DUPLICATE_IN_QUEUE",
        "Submission Duplicate In Queue",
    )
    SUBMISSION_DUPLICATE_EXISTING = (
        "SUBMISSION_DUPLICATE_EXISTING",
        "Submission Duplicate Existing",
    )
    SUBMISSION_PREVIOUSLY_REJECTED = (
        "SUBMISSION_PREVIOUSLY_REJECTED",
        "Submission Previously Rejected",
    )
    SUBMISSION_APPROVED = "SUBMISSION_APPROVED", "Submission Approved"
    SUBMISSION_REJECTED = "SUBMISSION_REJECTED", "Submission Rejected"
    SUBMISSION_PAYOUT_BYPASS_UPDATED = (
        "SUBMISSION_PAYOUT_BYPASS_UPDATED",
        "Submission Payout Bypass Updated",
    )


class WebhookDeliveryStatus(models.TextChoices):
    """Delivery bookkeeping for one outbound webhook attempt series."""

    PENDING = "PENDING", "Pending"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"
