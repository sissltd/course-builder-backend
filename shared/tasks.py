import logging

import httpx
from celery import shared_task

from shared.constants.environ import DJANGO_ENV

logger = logging.getLogger(__name__)


# >>>>>>>>>>>>EMAIL SEND LOGIC FOR EMAIL PROVIDERS: GMAIL, RESEND and CLOUDFLARE<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<


# send via gmail using Django's SMTP
def _send_via_gmail(
    subject: str,
    recipients: list[str],
    text_content: str,
    html_content: str,
    from_email: str | None = None,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    reply_to: list[str] | None = None,
    attachments: list[tuple] | None = None,
) -> dict:
    """
    Using EmailMultiAlternatives to attach both a plain-text body AND an HTML body.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        cc=cc_emails,
        bcc=bcc_emails,
        reply_to=reply_to,
    )
    message.attach_alternative(html_content, "text/html")

    for attachment in attachments or []:
        message.attach(*attachment)

    message.send()
    return {"status": "sent", "provider": "gmail"}


# send via resend
def _send_via_resend(
    subject: str,
    recipients: list[str],
    text_content: str,
    html_content: str,
    from_email: str | None = None,
) -> dict:
    import resend
    from decouple import config
    from django.conf import settings

    from shared.constants.authentication import COMPANY_NAME

    resend.api_key = config("RESEND_API_KEY")

    response = resend.Emails.send(
        {
            "from": f"{COMPANY_NAME} <{from_email or settings.DEFAULT_FROM_EMAIL}>",
            "to": recipients,
            "subject": str(subject),
            "text": str(text_content),
            "html": str(html_content),
        }
    )
    return {"status": "sent", "provider": "resend", "id": response.get("id")}


def _cloudflare_error_message(response: httpx.Response) -> str:
    """Extract Cloudflare's first reported error for a failed send response."""
    try:
        errors = response.json().get("errors") or []
        if errors:
            return f"{errors[0].get('code')}: {errors[0].get('message')}"
    except ValueError:
        pass
    return response.text[:300]


# send via Cloudflare Email Service REST API (HTTPS — no SMTP egress required)
def _send_via_cloudflare(
    subject: str,
    recipients: list[str],
    text_content: str,
    html_content: str,
    from_email: str | None = None,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    reply_to: str | list[str] | None = None,
) -> dict:
    """Send via Cloudflare Email Service's `POST .../email/sending/send` endpoint."""
    from django.conf import settings

    from shared.constants.authentication import COMPANY_NAME

    payload = {
        "from": {
            "address": from_email or settings.DEFAULT_FROM_EMAIL,
            "name": COMPANY_NAME,
        },
        "to": recipients,
        "subject": str(subject),
        "text": str(text_content),
        "html": str(html_content),
    }
    if cc_emails:
        payload["cc"] = cc_emails
    if bcc_emails:
        payload["bcc"] = bcc_emails
    if reply_to:
        payload["reply_to"] = reply_to[0] if isinstance(reply_to, list) else reply_to

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{settings.CLOUDFLARE_ACCOUNT_ID}/email/sending/send"
    )
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=httpx.Timeout(10.0, read=30.0),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Cloudflare send transport error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Cloudflare send failed (HTTP {response.status_code}): "
            f"{_cloudflare_error_message(response)}"
        )

    result = response.json().get("result", {})
    return {
        "status": "sent",
        "provider": "cloudflare",
        "id": result.get("message_id"),
    }


def dispatch_email(
    subject: str,
    recipients: list[str],
    text_content: str,
    html_content: str,
    from_email: str | None = None,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    reply_to: list[str] | None = None,
    attachments: list[tuple] | None = None,
) -> dict:
    """
    Central dispatcher — routes to the configured email provider.
    Provider is set via EMAIL_PROVIDER — "smtp"/"gmail" (Django SMTP),
    "resend" (Resend REST API), or "cloudflare" (Cloudflare Email Service REST API).

    This is the single place to add new providers.
    """
    from django.conf import settings

    provider = getattr(settings, "EMAIL_PROVIDER", "gmail")

    if provider == "resend":
        return _send_via_resend(
            subject=subject,
            recipients=recipients,
            text_content=text_content,
            html_content=html_content,
            from_email=from_email,
        )

    if provider == "cloudflare":
        return _send_via_cloudflare(
            subject=subject,
            recipients=recipients,
            text_content=text_content,
            html_content=html_content,
            from_email=from_email,
            cc_emails=cc_emails,
            bcc_emails=bcc_emails,
            reply_to=reply_to,
        )

    return _send_via_gmail(
        subject=subject,
        recipients=recipients,
        text_content=text_content,
        html_content=html_content,
        from_email=from_email,
        cc_emails=cc_emails,
        bcc_emails=bcc_emails,
        reply_to=reply_to,
        attachments=attachments,
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_templated_email_task(
    self,
    *,
    receivers: list[str],
    subject: str,
    template_name: str,
    context: dict | None = None,
    from_email: str | None = None,
    email_type: str = "TRANSACTIONAL",
) -> dict:
    """Render a Django email template and send it asynchronously.

    Accepts the same logical shape as Notification.emit_email_notification
    but takes plain email-address strings (not User instances) so it can be
    serialised by Celery.
    """
    try:
        # Local imports avoid circular dependency with api.notification.*
        from api.notification.services.email_service import send_templated_email

        sent_count = send_templated_email(
            receivers=receivers,
            subject=subject,
            template_name=template_name,
            context=context or {},
            from_email=from_email,
        )
        if sent_count < 1:
            raise RuntimeError("Email provider accepted zero messages.")
        from django.conf import settings

        provider = getattr(settings, "EMAIL_PROVIDER", "gmail")
        logger.info(
            "auth_email_sent email_type=%s provider=%s recipients=%s subject=%s "
            "sent_count=%s task_id=%s",
            email_type,
            provider,
            receivers,
            subject,
            sent_count,
            self.request.id,
        )
        return {
            "status": "sent",
            "email_type": email_type,
            "provider": provider,
            "recipients": receivers,
            "subject": subject,
            "sent_count": sent_count,
            "task_id": self.request.id,
        }
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(
                "auth_email_failed email_type=%s recipients=%s subject=%s "
                "attempts=%s task_id=%s error=%s",
                email_type,
                receivers,
                subject,
                self.request.retries + 1,
                self.request.id,
                exc,
            )
            raise
        logger.exception(
            "auth_email_retrying email_type=%s recipients=%s subject=%s "
            "attempt=%s max_retries=%s task_id=%s error=%s",
            email_type,
            receivers,
            subject,
            self.request.retries + 1,
            self.max_retries,
            self.request.id,
            exc,
        )
        raise self.retry(exc=exc)


# >>>>>>>>>>>>General Shared Tasks<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<


# This is a shared task for sending email generally to users
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(
    self,
    subject: str,
    recipient: str,
    html_content: str,
    text_content: str | None = None,
):
    try:
        # Generate a basic text fallback if not provided
        if text_content is None:
            text_content = f"{subject}\n\n{html_content}"

        result = dispatch_email(
            subject=subject,
            recipients=[recipient],
            text_content=text_content,
            html_content=html_content,
        )
        logger.info(
            "email_sent provider=%s recipient=%s subject=%s task_id=%s",
            result.get("provider"),
            recipient,
            subject,
            self.request.id,
        )
        return result

    except Exception as e:
        if self.request.retries >= self.max_retries:
            logger.exception(
                "email_failed recipient=%s subject=%s attempts=%s task_id=%s error=%s",
                recipient,
                subject,
                self.request.retries + 1,
                self.request.id,
                e,
            )
            raise
        logger.exception(
            "email_retrying recipient=%s subject=%s attempt=%s max_retries=%s "
            "task_id=%s error=%s",
            recipient,
            subject,
            self.request.retries + 1,
            self.max_retries,
            self.request.id,
            e,
        )
        raise self.retry(exc=e)


# >>>>>>>>>>>>>>>OTP Email Task<<<<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<

# This is a shared task for sending otp email to users (this is made separate because we want the otp to be logged in celery in dev/staging but to be sent in prod.)


@shared_task(
    bind=True, max_retries=3, default_retry_delay=60, name="shared.send_otp_email"
)
def send_otp_email(self, email, otp):
    """
    Two logics:


    In dev: It logs to the console (can be filtered by docker logs -f celery)

    In prod: It actually sends to the user email using the _dispatch_email and verification_email template (templates/emails/verification_email.py)
    """

    try:
        # >>>>>>>>>>>>>>>>>>> DEV / STAGING <<<<<<<<<<<<<<<<<<<<
        if DJANGO_ENV in (
            "dev",
            "development",
            "pre-production",
        ):  # Can add more env for testing
            # TODO: Will modify the logger to carry the user role (homeowner/artisan/vendor/company/staff) in future..
            logger.info(
                "\n" + "=" * 50 + "\n"
                f"  OTP for {email}\n"
                f"  Code   : {otp}\n"
                f"  Expires: 5 minutes\n" + "=" * 50
            )

            # Optional, you can choose to configure it or remove it as desired
            return {"status": "logged", "env": DJANGO_ENV}

        # >>>>>>>>>>>>>>>>>>>>>>>>> PROD <<<<<<<<<<<<<<<<<<<<<<<<<<
        from django.template.loader import render_to_string
        from django.template.exceptions import TemplateDoesNotExist

        from shared.constants.authentication import (
            COMPANY_NAME,
            SUPPORT_EMAIL,
        )

        # payload
        context = {
            "user_name": email,
            "verification_code": otp,
            "company_name": COMPANY_NAME,
            "support_email": SUPPORT_EMAIL,
        }

        html_content = render_to_string("emails/verification_email.html", context)

        # Try to render text template, fallback to simple text if missing
        try:
            text_content = render_to_string("emails/verification_email.txt", context)
        except TemplateDoesNotExist:
            text_content = (
                f"Your login code is: {otp}\n\n"
                f"This code expires in 5 minutes.\n\n"
                f"If you didn't request this, please ignore this email.\n\n"
                f"– {COMPANY_NAME} ({SUPPORT_EMAIL})"
            )

        # send email payload
        result = dispatch_email(
            subject="Your Login code",
            recipients=[email],
            text_content=text_content,
            html_content=html_content,
        )

        logger.info(f"OTP sent successfully to: {email}")
        return result

    except Exception as exc:
        logger.error(f"Failed to send OTP to {email}: {exc}")
        raise self.retry(exc=exc)


# >>>>>>>>>>>>>>>OTP Audit Log<<<<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<


# This is the shared log for create audit log
@shared_task(
    bind=True, max_retries=2, default_retry_delay=10, name="shared.create_audit_log"
)
def create_audit_log_task(
    self,
    event,
    email,
    ip_address=None,
    user_agent="",
    metadata=None,
):
    """Creates an audit log entry."""
    try:
        from shared.audit.models import AuditLog

        AuditLog.objects.create(
            event=event,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent or "",
            metadata=metadata or {},
        )
        logger.info(f"Audit log created: {event} - {email}")
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        raise self.retry(exc=e)


# This is a shared task to write audit log of event done on an email
@shared_task(
    bind=True, max_retries=5, default_retry_delay=30, name="shared.write_audit_log"
)
def write_audit_log(self, event, email, ip, meta=None):
    """Creates an audit log entry."""
    try:
        from shared.audit.models import AuditLog

        AuditLog.objects.create(
            event=event,
            email=email,
            ip_address=ip,
            metadata=meta or {},
        )
        logger.info(f"[:::] Audit: [{event}] {email} from {ip} ")
    except Exception as exc:
        raise self.retry(exc=exc)
