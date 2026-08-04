import logging

import resend
from decouple import config

from shared.constants.environ import DJANGO_ENV

# Configure logging
logger = logging.getLogger(__name__)


def send_email(subject, recipient, template):
    if (
        DJANGO_ENV == "development"
        or DJANGO_ENV == "pre-production"
    ):
        
        print(">>> IGNORING SEND EMAIL")
        return
    try:
        print(">>> SENDING EMAIL ")
        resend.api_key = config("RESEND_API_KEY")

        response = resend.Emails.send(
            {
                "from": "FlashChange <mail@flashchange.io>",
                "to": str(recipient),
                "subject": str(subject),
                "html": str(template),
            }
        )

        logger.info(f"Email sent successfully to {recipient} with subject: {subject}")
        return response
    except Exception as e:
        logger.error(f"Failed to send email to {recipient}. Error: {e}")
        return None
