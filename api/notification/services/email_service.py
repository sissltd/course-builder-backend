from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from shared.tasks import dispatch_email


def send_templated_email(
    *,
    receivers: list[str],
    subject: str,
    template_name: str,
    context: dict | None = None,
    from_email: str | None = None,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    reply_to: list[str] | None = None,
    attachments: list[tuple] | None = None,
    fail_silently: bool = False,
) -> int:
    """Render and send a multipart (HTML + plain text) templated email.

    Routes via EMAIL_PROVIDER ("gmail" SMTP or "resend" REST API).
    Falls back to Django's EmailMultiAlternatives for advanced features
    (attachments, cc, bcc, reply_to) which are not yet supported on the resend path.
    """

    context = context or {}
    text_body = render_to_string(f"{template_name}.txt", context)
    html_body = render_to_string(f"{template_name}.html", context)

    provider = getattr(settings, "EMAIL_PROVIDER", "gmail")

    # Use the unified dispatcher for the common case (no advanced features)
    if provider == "resend" and not (attachments or cc_emails or bcc_emails or reply_to):
        dispatch_email(
            subject=subject,
            recipients=receivers,
            text_content=text_body,
            html_content=html_body,
            from_email=from_email,
        )
        return 1

    # Fallback to Django SMTP backend for advanced features or when using gmail provider
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=receivers,
        cc=cc_emails,
        bcc=bcc_emails,
        reply_to=reply_to,
    )
    message.attach_alternative(html_body, "text/html")

    for attachment in attachments or []:
        message.attach(*attachment)

    return message.send(fail_silently=fail_silently)
