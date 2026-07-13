"""Plain-function test object builders (no factory_boy dependency).

Self-contained (does not import api.courses.tests.factories) to keep
api.authentication's test suite independent of unrelated app boundaries.
"""

import hashlib
from datetime import timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.utils import timezone

from api.authentication.models import EmailVerificationToken

User = get_user_model()

_sequence = count(1)


def make_user(*, is_active=True, password="testpass123", **kwargs):
    n = next(_sequence)
    defaults = {
        "email": f"authuser{n}@example.com",
        "first_name": "Test",
        "last_name": "User",
        "is_active": is_active,
    }
    defaults.update(kwargs)
    return User.objects.create_user(password=password, **defaults)


def make_verification_token(
    *, user, purpose, raw_token="fixed-test-token-value", expires_in_minutes=60, is_used=False, attempts=0
):
    return EmailVerificationToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(minutes=expires_in_minutes),
        is_used=is_used,
        attempts=attempts,
    )
