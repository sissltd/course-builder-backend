"""Shared Celery task helpers.

This module provides synchronous email and OTP delivery as well as
Celery-based audit-log writing.  Email is sent inline (via Django's
``EmailMultiAlternatives``) because on Dokploy the web process and the
Celery worker may hit different Redis instances, making ``.delay()``
delivery unreliable for critical messages.

Example:
    from shared.celery.celery_service import CeleryService
    CeleryService.send_email("subj", "to@example.com", "<html>")
"""

from __future__ import annotations

import json
import logging

from django.core.mail import EmailMultiAlternatives
from django.core.serializers.json import DjangoJSONEncoder

from shared.tasks import write_audit_log

logger = logging.getLogger(__name__)


class CeleryService:
    """Helper wrapper for email delivery and audit-log writing."""

    @staticmethod
    def send_email(subject: str, recipient: str, html_content: str):
        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body="",
                from_email=None,
                to=[recipient],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
            logger.info("celery_service_email_sent recipient=%s subject=%s", recipient, subject)
            return True
        except Exception:
            logger.exception(
                "celery_service_email_failed recipient=%s subject=%s", recipient, subject
            )
            return False

    @staticmethod
    def send_otp_email(email: str, otp: str):
        from shared.tasks import send_otp_email as _send_otp_email

        return _send_otp_email.delay(email, otp)

    @staticmethod
    def write_audit_log(
        event: str, email: str, ip: str | None = None, meta: dict | None = None
    ):
        safe_meta = json.loads(json.dumps(meta or {}, cls=DjangoJSONEncoder))
        return write_audit_log.delay(event, email, ip, safe_meta)
