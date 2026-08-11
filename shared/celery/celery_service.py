"""Shared Celery task helpers.

This module provides a single place to import and call common Celery tasks.
It wraps the task call sites to keep call-sites consistent and reduce
dependency on task module layout.

Example:
    from shared.celery.celery_service import CeleryService
    CeleryService.send_email("subj", "to@example.com", "<html>")
"""

from __future__ import annotations

import json

from django.core.serializers.json import DjangoJSONEncoder

from shared.tasks import send_email_task, send_otp_email, write_audit_log


class CeleryService:
    """Helper wrapper for common Celery tasks."""

    @staticmethod
    def send_email(subject: str, recipient: str, html_content: str):
        return send_email_task.delay(subject, recipient, html_content)

    @staticmethod
    def send_otp_email(email: str, otp: str):
        return send_otp_email.delay(email, otp)

    @staticmethod
    def write_audit_log(
        event: str, email: str, ip: str | None = None, meta: dict | None = None
    ):
        # Sanitize meta through DjangoJSONEncoder so that datetime/UUID/Decimal
        # values from QuerySet.values() don't cause "Object of type datetime is
        # not JSON serializable" when Celery serializes the task arguments.
        safe_meta = json.loads(json.dumps(meta or {}, cls=DjangoJSONEncoder))
        return write_audit_log.delay(event, email, ip, safe_meta)
