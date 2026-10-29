import logging
from urllib.parse import urlencode

from django.conf import settings
from django.template.loader import render_to_string

from shared.constants.authentication import (
    COMPANY_NAME,
    FRONTEND_URL,
    STAFF_INVITATION_EXPIRY_HOURS,
    SUPPORT_EMAIL,
)
from shared.tasks import send_email_task

logger = logging.getLogger(__name__)


class EmailService:
    @staticmethod
    def _send_email(subject, recipient, html_content):

        try:
            send_email_task.delay(subject, str(recipient), str(html_content))  # type: ignore
            logger.info(f"Email queued for {recipient} with subject: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to queue email for {recipient}. Error: {e}")
            return None

    @staticmethod
    def send_password_reset_email(user_email, reset_token, user_name):
        try:
            query_params = urlencode(
                {
                    "email": user_email,
                    "otp": reset_token,
                }
            )
            reset_link = f"{FRONTEND_URL}/auth/reset-password?{query_params}"
            context = {
                "user_name": user_name,
                "reset_token": reset_token,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
                "reset_link": reset_link,
            }

            html_content = render_to_string("emails/password_reset_email.html", context)

            subject = "Reset Your Password"

            response = EmailService._send_email(subject, user_email, html_content)

            if response:
                return reset_link
            return None

        except Exception as e:
            logger.error(f"Error sending password reset email: {e}")
            return None

    @staticmethod
    def send_staff_invitation_email(
        user_email, user_name, host_name, accept_url, designation=""
    ):
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "host_name": host_name,
                "designation": designation,
                "accept_url": accept_url,
                "expiry_text": f"{STAFF_INVITATION_EXPIRY_HOURS} hours",
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/staff_invitation_email.html", context
            )
            subject = f"You're Invited to Join {COMPANY_NAME}"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending staff invitation email: {e}")
            return False

    @staticmethod
    def send_withdrawal_otp_email(user_email, first_name, code, amount):
        try:
            context = {
                "first_name": first_name,
                "amount": amount,
                "code": code,
                "expiry_minutes": settings.WITHDRAWAL_OTP_EXPIRY_MINUTES,
            }

            # if DJANGO_ENV == "development":
            #     logger.warning(context)
            #     return True
            html_content = render_to_string("emails/withdrawal_otp.html", context)
            subject = "Verify Your Withdrawal Request"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending withdrawal OTP email: {e}")
            return False
