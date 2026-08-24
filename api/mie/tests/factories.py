"""Plain-function test object builder for the MIE models (no factory_boy
dependency), matching the style of api.collaborators.tests.factories."""

from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from django.utils import timezone

from api.mie.enums import (
    DeveloperAccountStatus,
    MiePlanType,
    SubmissionStatus,
    WebhookEventType,
)
from api.mie.models import (
    CourseSubmission,
    DeveloperAccount,
    SubmissionRejectionReason,
    WebhookEvent,
)


def make_developer_account(
    *,
    email=None,
    status=DeveloperAccountStatus.PENDING,
    plan_type=MiePlanType.PAID_PER_SUBMISSION,
    **kwargs,
):
    defaults = {
        "email": email or f"dev-{uuid4().hex[:8]}@example.com",
        "webhook_url": f"https://hooks.example.com/{uuid4().hex[:8]}",
        "status": status,
        "plan_type": plan_type,
    }
    defaults.update(kwargs)
    return DeveloperAccount.objects.create(**defaults)


def make_approved_account(**kwargs):
    """Approved account with no key material yet - the exact state an
    approval leaves before credentials are issued onto it."""

    kwargs.setdefault("status", DeveloperAccountStatus.APPROVED)
    kwargs.setdefault("decided_at", timezone.now())
    return make_developer_account(**kwargs)


def make_approved_developer(**kwargs):
    """Approved account carrying stored key material exactly as approval
    would leave it. Returns (account, raw_key); only the hash is persisted."""
    raw = f"scb_live_{uuid4().hex}"
    defaults = {
        "status": DeveloperAccountStatus.APPROVED,
        "api_key_prefix": raw[:16],
        "api_key_hash": sha256(raw.encode()).hexdigest(),
        "signing_secret": uuid4().hex,
        "decided_at": timezone.now(),
    }
    defaults.update(kwargs)
    return make_developer_account(**defaults), raw


def make_rejection_reason(*, label=None, is_active=True, **kwargs):
    defaults = {
        "label": label or f"Reason {uuid4().hex[:8]}",
        "is_active": is_active,
    }
    defaults.update(kwargs)
    return SubmissionRejectionReason.objects.create(**defaults)


def make_submission(
    *,
    developer=None,
    title=None,
    status=SubmissionStatus.PENDING_REVIEW,
    payload=None,
    **kwargs,
):
    if developer is None:
        developer = make_developer_account()
    resolved_title = title or f"Idea {uuid4().hex[:8]}"
    defaults = {
        "developer": developer,
        "title": resolved_title,
        "status": status,
        "payload": payload or {"title": resolved_title, "description": "x"},
        "queued_at": timezone.now() - timedelta(minutes=5),
    }
    defaults.update(kwargs)
    return CourseSubmission.objects.create(**defaults)


def make_decided_submission(*, approved=True, decided_by=None, **kwargs):
    """Submission already past review, as a superadmin decision leaves it."""
    defaults = {
        "status": SubmissionStatus.APPROVED if approved else SubmissionStatus.REJECTED,
        "decided_at": timezone.now(),
        "decided_by": decided_by,
        "queued_at": timezone.now() - timedelta(days=1),
    }
    defaults.update(kwargs)
    return make_submission(**defaults)


def make_webhook_event(*, submission=None, event_type=WebhookEventType.SUBMISSION_QUEUED, **kwargs):
    if submission is None:
        submission = make_submission()
    defaults = {
        "submission": submission,
        "event_type": event_type,
        "payload": {
            "submission": {
                "reference": submission.public_reference,
                "status": submission.status,
                "title": submission.title,
            },
            "developer_email": submission.developer.email,
        },
        "attempts": 0,
    }
    defaults.update(kwargs)
    return WebhookEvent.objects.create(**defaults)
