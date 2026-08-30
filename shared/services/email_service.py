import logging
from urllib.parse import urlencode

from django.template.loader import render_to_string

from shared.constants.authentication import (
    COMPANY_NAME,
    FRONTEND_URL,
    STAFF_INVITATION_EXPIRY_HOURS,
    SUPPORT_EMAIL,
)
from shared.constants.environ import DJANGO_ENV

logger = logging.getLogger(__name__)


class EmailService:
    @staticmethod
    def _send_email(subject, recipient, html_content):
        from shared.tasks import dispatch_email

        try:
            dispatch_email(
                subject=subject,
                recipients=[str(recipient)],
                # dispatch_email mirrors EMAIL_SERVICE.md: "gmail" (SMTP) and
                # "resend" providers both need a plain-text body. Reuse the
                # subject + html as the fallback, matching send_email_task.
                text_content=f"{subject}\n\n{html_content}",
                html_content=str(html_content),
            )
            logger.info(f"Email sent to {recipient} with subject: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {recipient}. Error: {e}")
            return None

    @staticmethod
    def send_verification_email(user_email, verification_code, user_name):
        try:
            context = {
                "user_name": user_name,
                "verification_code": verification_code,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string("emails/verification_email.html", context)

            subject = "Verify Your Email Address"

            response = EmailService._send_email(subject, user_email, html_content)
            print(">>> RESPONSE >>>>")
            print(response)
            if response:
                print(">>> SENT")
                return True
            return False

        except Exception as e:
            logger.error(f"Error sending verification email: {e}")
            return False

    @staticmethod
    def send_welcome_email(user_email, user_name):
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
                "login_url": f"{FRONTEND_URL}/login",
            }

            html_content = render_to_string("emails/welcome_email.html", context)

            subject = "Welcome to Feexeet!"

            response = EmailService._send_email(subject, user_email, html_content)

            return bool(response)

        except Exception as e:
            logger.error(f"Error sending welcome email: {e}")
            return False

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
    def send_vendor_invitation_email(vendor_email, vendor_name=""):
        """
        Sends a vendor invitation email.
        """
        try:
            first_name = (
                vendor_name.split()[0] if vendor_name else vendor_email.split("@")[0]
            )
            signup_url = f"{FRONTEND_URL}/vendor/register"

            context = {
                "FirstName": first_name,
                "vendor_name": vendor_name,
                "signup_url": signup_url,
                "RecipientEmail": vendor_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/vendor_invitation_email.html", context
            )
            subject = f"You're Invited to Join {COMPANY_NAME} as a Vendor"

            response = EmailService._send_email(subject, vendor_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(
                f"Error sending vendor invitation email to {vendor_email}: {e}"
            )
            return False

    @staticmethod
    def send_occupant_invitation_email(
        occupant_email, occupant_name="", home_unit_name="", estate_name=""
    ):
        """
        Sends a B2B occupant (tenant) invitation email — "join your home".
        """
        try:
            first_name = (
                occupant_name.split()[0]
                if occupant_name
                else occupant_email.split("@")[0]
            )
            signup_url = f"{FRONTEND_URL}/occupant/register"

            context = {
                "FirstName": first_name,
                "occupant_name": occupant_name,
                "home_unit_name": home_unit_name,
                "estate_name": estate_name,
                "signup_url": signup_url,
                "RecipientEmail": occupant_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/occupant_invitation_email.html", context
            )
            subject = f"You've been invited to join your home on {COMPANY_NAME}"

            response = EmailService._send_email(subject, occupant_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(
                f"Error sending occupant invitation email to {occupant_email}: {e}"
            )
            return False

    @staticmethod
    def send_courier_org_invitation_email(org_email, organization_name=""):
        """
        Sends a courier-organization invitation email.
        """
        try:
            first_name = (
                organization_name.split()[0]
                if organization_name
                else org_email.split("@")[0]
            )
            signup_url = f"{FRONTEND_URL}/courier-organization/register"

            context = {
                "FirstName": first_name,
                "organization_name": organization_name,
                "signup_url": signup_url,
                "RecipientEmail": org_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/courier_org_invitation_email.html", context
            )
            subject = f"You're Invited to Join {COMPANY_NAME} as a Courier Organisation"

            response = EmailService._send_email(subject, org_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(
                f"Error sending courier org invitation email to {org_email}: {e}"
            )
            return False

    @staticmethod
    def send_superadmin_invitation_email(admin_email, admin_name="", accept_url=""):
        """
        Sends a SuperAdmin invitation email with a token-based activation link.
        Mirrors send_staff_invitation_email().
        """
        try:
            first_name = (
                admin_name.split()[0] if admin_name else admin_email.split("@")[0]
            )
            context = {
                "FirstName": first_name,
                "admin_name": admin_name,
                "signup_url": accept_url,
                "accept_url": accept_url,
                "RecipientEmail": admin_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/superadmin_invitation_email.html", context
            )
            subject = f"You've Been Invited as a SuperAdmin on {COMPANY_NAME}"

            response = EmailService._send_email(subject, admin_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(
                f"Error sending SuperAdmin invitation email to {admin_email}: {e}"
            )
            return False

    @staticmethod
    def send_payment_confirmation_email(
        user_email, user_name, amount, transaction_id, booking_id, currency="NGN"
    ):
        """Send payment confirmation email"""
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "Amount": f"{amount:,.2f}",
                "TransactionID": transaction_id,
                "BookingID": booking_id,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/payment_confirmation_email.html", context
            )
            subject = "Payment Confirmation"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending payment confirmation email: {e}")
            return False

    @staticmethod
    def send_password_changed_email(user_email, user_name):
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/password_changed_email.html", context
            )
            subject = "Your Password Has Been Changed"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending password changed email: {e}")
            return False

    @staticmethod
    def send_account_deactivated_email(user_email, user_name, status, reason=None):
        """Send account deactivated/deleted email"""
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "Status": status,  # "deactivated" or "deleted"
                "Reason": reason,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/account_deactivated_email.html", context
            )
            subject = f"Account {status.title()} - Feexeet"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending account deactivated email: {e}")
            return False

    @staticmethod
    def send_support_request_update_email(
        user_email, user_name, ticket_id, status, admin_message=None
    ):
        """Send support request update email"""
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "TicketID": ticket_id,
                "Status": status,
                "AdminMessage": admin_message,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/support_request_update_email.html", context
            )
            subject = f"Support Request Update - Ticket #{ticket_id}"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending support request update email: {e}")
            return False

    @staticmethod
    def send_host_account_created_email(user_email, user_name):
        """Send host account created email"""
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/host_account_created_email.html", context
            )
            subject = "Host Account Created Successfully"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending host account created email: {e}")
            return False

    @staticmethod
    def send_admin_account_created_email(user_email, user_name, role, password=None):
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "RecipientEmail": user_email,
                "user_email": user_email,
                "role": role,
                "password": password,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
                "login_url": "https://feexeet.com/auth/login",
            }

            html_content = render_to_string(
                "emails/admin_account_created_email.html", context
            )
            subject = "Admin Account Created - Feexeet"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending admin account created email: {e}")
            return False

    @staticmethod
    def send_payout_sent_email(user_email, user_name, amount, currency="NGN"):
        """Send payout sent email to host"""
        try:
            first_name = user_name.split()[0] if user_name else "User"
            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "Amount": f"{amount:,.2f}",
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string("emails/payout_sent_email.html", context)
            subject = f"Payout Sent - N{amount:,.2f}"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending payout sent email: {e}")
            return False

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
    def send_withdrawal_otp_email(user_email, user_name, otp_code, amount):
        try:
            context = {
                "user_name": user_name,
                "amount": amount,
                "verification_code": otp_code,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            if DJANGO_ENV == "development":
                logger.warning(context)
                return True
            html_content = render_to_string("emails/withdrawal_otp_email.html", context)
            subject = "Verify Your Withdrawal Request"

            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending withdrawal OTP email: {e}")
            return False

    @staticmethod
    def send_booking_status_email(
        user_email,
        user_name,
        booking,
        subject,
        message,
        extra_message=None,
        guest_name=None,
        host_name=None,
    ):
        try:
            first_name = user_name.split()[0] if user_name else "User"
            dates = f"{booking.check_in.strftime('%B %d')} - {booking.check_out.strftime('%B %d, %Y')}"

            context = {
                "FirstName": first_name,
                "user_name": user_name,
                "PropertyName": booking.listing.property_name,
                "Dates": dates,
                "GuestName": guest_name or "",
                "HostName": host_name or "",
                "message": message,
                "extra_message": extra_message or "",
                "RecipientEmail": user_email,
                "user_email": user_email,
                "company_name": COMPANY_NAME,
                "support_email": SUPPORT_EMAIL,
            }

            html_content = render_to_string(
                "emails/booking_status_change_email.html", context
            )
            response = EmailService._send_email(subject, user_email, html_content)
            return bool(response)

        except Exception as e:
            logger.error(f"Error sending booking status email: {e}")
            return False
