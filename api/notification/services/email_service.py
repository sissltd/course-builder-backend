from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


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

    Renders `templates/{template_name}.html` and `templates/{template_name}.txt`
    via render_to_string, then sends an EmailMultiAlternatives with the text
    version as the body and the HTML version attached as an alternative.
    `attachments` is a list of (filename, content, mimetype) tuples, matching
    Django's EmailMessage.attach() signature. Returns the number of successfully
    delivered messages (0 or 1, per Django's EmailMessage.send()).
    """

    context = context or {}
    text_body = render_to_string(f"{template_name}.txt", context)
    html_body = render_to_string(f"{template_name}.html", context)

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
