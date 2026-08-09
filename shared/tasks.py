import logging

from celery import shared_task

from shared.constants.environ import DJANGO_ENV

logger = logging.getLogger(__name__)



# >>>>>>>>>>>>CREATING EMAIL SEND LOGIC TASK FOR EMAIL PROVIDERS: GMAIL and RESEND<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<

# send via gmail using Django's SMTP
def _send_via_gmail(subject: str, recipient: str, html_content: str) -> dict:   
    """
    Using EmailMultiAlternatives as it allow us to attach both a plain-text body AND an HTML body. to avoid our messages going into the spam folder, we make the HTML an "alternative" — to make the client pick the best one.
    
    """
    
    # importing inside here to avoid circular imports
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives

    # if the email is not compatible in the mail client, we will use this plain text
    plain_text = (
        "[Incompatible] Please open this email in an HTML-compatible mail client (e.g., google mail, yahoo mail, etc)"
    )

    # message body
    message = EmailMultiAlternatives(
        subject=subject,
        body=plain_text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )

    message.attach_alternative(html_content, "text/html")

    # send message
    message.send()

    return {"status": "sent", "provider": "gmail"}


# send via resend
def _send_via_resend(subject: str, recipient: str, html_content: str) -> dict:
    """
    Normal setup, aside from the importing of resend and config inside the function - not at module level - to
    avoid import errors on machines where RESEND_API_KEY isn't set — that is, where (team) is testing only on gmail

    """

    # Import resend and config
    import resend
    from decouple import config
    from django.conf import settings

    # resend configuration
    resend.api_key = config("RESEND_API_KEY")
    
    # sending email payload
    response = resend.Emails.send(
            {
                "from": f"Feexeet <{settings.DEFAULT_FROM_EMAIL}>",
                "to": str(recipient),
                "subject": str(subject),
                "html": str(html_content),
            }
    )
    
    return {"status": "sent", "provider": "resend", "id": response.get("id")}
    
# the general function that dispatches the email
def _dispatch_email(subject: str, recipient: str, html_content: str) -> dict:
    """
    This is the only place in the codebase that will call the two providers to make switching easy
    
    Routes to the configured email provider.
    Provider is set via EMAIL_PROVIDER in .env — "gmail" (default) or "resend".
    
    Every task calls this — so switching providers is a single .env change. (EMAIL_PROVIDER)
    """
    
    from django.conf import settings

    # get the provider (defaulting to gmail)
    provider = getattr(settings, "EMAIL_PROVIDER", "gmail")
    
    # the switch
    if provider == "resend":
        return _send_via_resend(subject, recipient, html_content)
    
    # else send via gmail
    return _send_via_gmail(subject, recipient, html_content)
    

# >>>>>>>>>>>>General Shared Tasks<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<

# This is a shared task for sending email generally to users
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, subject: str, recipient: str, html_content: str):
    try:
        
        # Now we will use dispatch email to send email task
        result = _dispatch_email(subject, recipient, html_content)
        logger.info(f"Email sent successfully to {recipient} with subject: {subject}")
        return result
    
    except Exception as e:
        logger.error(f"Failed to send email to {recipient}. Error: {e}")
        raise self.retry(exc=e)
    

# >>>>>>>>>>>>>>>OTP Email Task<<<<<<<<<<<<<<<<<<<<<<<<
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<

# This is a shared task for sending otp email to users (this is made separate because we want the otp to be logged in celery in dev/staging but to be sent in prod.)

@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="shared.send_otp_email")
def send_otp_email(self, email, otp):
    """
    Two logics:
    
    
    In dev: It logs to the console (can be filtered by docker logs -f celery)
    
    In prod: It actually sends to the user email using the _dispatch_email and verification_email template (templates/emails/verification_email.py)
    """
    
    try:
        # >>>>>>>>>>>>>>>>>>> DEV / STAGING <<<<<<<<<<<<<<<<<<<<
        if DJANGO_ENV in ("dev", "development", "pre-production"): # Can add more env for testing
            
            # TODO: Will modify the logger to carry the user role (homeowner/artisan/vendor/company/staff) in future..
            logger.info(
                "\n" + "=" * 50 + "\n"
                f"  OTP for {email}\n"
                f"  Code   : {otp}\n"
                f"  Expires: 5 minutes\n"
                + "=" * 50
            )
            
            # Optional, you can choose to configure it or remove it as desired
            return {"status": "logged", "env": DJANGO_ENV}
        
        # >>>>>>>>>>>>>>>>>>>>>>>>> PROD <<<<<<<<<<<<<<<<<<<<<<<<<<
        from django.template.loader import render_to_string

        from shared.constants.authentication import (  # TODO: Will add more imports after authentication is fully integrated
            COMPANY_NAME,
            SUPPORT_EMAIL,
        )

        # payload 
        # TODO: Will configure the context well in future
        context = {
            "user_name": email,
            "verification_code": otp,
            "company_name": COMPANY_NAME,
            "support_email": SUPPORT_EMAIL,            
        }
        
        html_content = render_to_string("emails/verification_email.html", context)
        
        # send email payload
        result = _dispatch_email(
            subject="Your Login code",
            recipient=email,
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
@shared_task(bind=True, max_retries=2, default_retry_delay=10, name="shared.create_audit_log")
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
        from shared.auditt.models import AuditLog

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
@shared_task(bind=True, max_retries=5, default_retry_delay=30, name="shared.write_audit_log")
def write_audit_log(self, event, email, ip, meta=None):
    """Creates an audit log entry."""
    try:
        from shared.auditt.models import AuditLog

        AuditLog.objects.create(
            event=event,
            email=email,
            ip_address=ip,
            metadata=meta or {},
        )
        logger.info(f"[:::] Audit: [{event}] {email} from {ip} ")
    except Exception as exc:
        raise self.retry(exc=exc)

