import hashlib
import hmac
import json
import logging
from decimal import Decimal
from typing import Any

import requests
from decouple import config

from shared.constants.error import extract_response_error
from shared.constants.paystack import PAYSTACK_MULTIPLIER
from shared.exceptions.paystack_exception import PaystackServiceError

logger = logging.getLogger(__name__)


class PaystackService:
    BASE_URL = "https://api.paystack.co"
    TIMEOUT = 10

    @staticmethod
    def _get_headers():
        secret_key = config("PAYSTACK_SECRET_KEY", default="")
        if not secret_key:
            logger.warning("PAYSTACK_SECRET_KEY is not set in environment variables.")
        return {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def resolve_bank(account_number: str, bank_code: str) -> dict[str, Any]:
        """
        Resolve using Paystack API

        Args:
            account_number: Account number
            bank_code: Bank code

        Returns:
            dict: Paystack API response
        """

        url = f"{PaystackService.BASE_URL}/bank/resolve?account_number={account_number}&bank_code={bank_code}"
        response = requests.get(
            url,
            headers=PaystackService._get_headers(),
            timeout=PaystackService.TIMEOUT,
        )

        if response.status_code >= 400:
            error_message = extract_response_error(response)
            raise PaystackServiceError(error_message)

        result = response.json()

        return result.get("data", {})

    @staticmethod
    def verify_webhook_signature(request_body, signature_header):

        secret_key = config("PAYSTACK_SECRET_KEY", default="")
        if not secret_key:
            logger.error("PAYSTACK_SECRET_KEY missing for webhook verification")
            return False

        if isinstance(request_body, str):
            request_body = request_body.encode("utf-8")

        hash_object = hmac.new(secret_key.encode("utf-8"), msg=request_body, digestmod=hashlib.sha512) # type: ignore
        expected_signature = hash_object.hexdigest()

        return hmac.compare_digest(expected_signature, signature_header)

    @staticmethod
    def initialize_payment(email: str, amount: Decimal, callback_url: str|None=None, metadata: dict|None=None) -> tuple[bool, dict]:
        """
        Initialize payment using Paystack API

        Args:
            email: customer email
            amount: amount to pay
            callback_url: callback url to redirect users to after making payment (optional)
            metadata: data to pass to paystack (optional)

        Returns:
            dict
        """

        url = f"{PaystackService.BASE_URL}/transaction/initialize"
        payload = {
            "email": email,
            "amount": str(int(amount * PAYSTACK_MULTIPLIER)),  # Convert amount to Kobo (1 Naira = 100 Kobo)
            "callback_url": callback_url,
            "metadata": metadata,
            "channels": ["card", "bank_transfer"],
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=PaystackService._get_headers(),
                timeout=PaystackService.TIMEOUT,
            )
            response_data = response.json()

            if response.status_code == 200 and response_data.get("status"):
                return True, {"message": response_data.get("data")}

            logger.error(f"Paystack initialize failed: {response_data.get('message')}")
            return False, {"message": {'message': response_data.get("message")}}

        except requests.exceptions.RequestException as e:
            logger.error(f"Error initializing Paystack payment: {e}")
            return False, {"message": {'message': str(e)}}

    @staticmethod
    def verify_payment(reference):

        url = f"{PaystackService.BASE_URL}/transaction/verify/{reference}"

        try:
            response = requests.get(
                url,
                headers=PaystackService._get_headers(),
                timeout=PaystackService.TIMEOUT,
            )
            response_data = response.json()

            if response.status_code == 200 and response_data.get("status"):
                return True, response_data.get("data")

            logger.error(f"Paystack verification failed: {response_data.get('message')}")
            return False, {"message": f"Paystack verification failed: {response_data.get('message')}"}

        except requests.exceptions.RequestException as e:
            logger.error(f"Error verifying Paystack payment: {e}")
            return False, {"message": str(e)}

    @staticmethod
    def get_banks():
        from shared.redis.redis_service import RedisService

        cached = RedisService.get_cached_banks()
        if cached is not None:
            return cached
        url = f"{PaystackService.BASE_URL}/bank?currency=NGN&enabled_for_verification=true"
        response = requests.get(
            url,
            headers=PaystackService._get_headers(),
            timeout=PaystackService.TIMEOUT,
        )
        if response.status_code >= 400:
            error_message = extract_response_error(response)
            raise PaystackServiceError(error_message)
        result = response.json()

        RedisService.set_cached_banks(result)
        return result

    @staticmethod
    def get_bank_name(bank_code):
        banks_resp = PaystackService.get_banks()
        bank_list = banks_resp["data"]
        bank = next(bank for bank in bank_list if bank["code"] == bank_code)
        bank_name = bank["name"]
        return bank_name

    @staticmethod
    def create_transfer_recipient(account_number: str, bank_code: str, name: str) -> tuple[bool, dict]:
        """
        Create a Paystack transfer recipient (NUBAN).

        Args:
            account_number: Staff bank account number
            bank_code:      Bank code (e.g. "058" for GTBank)
            name:           Account holder name

        Returns:
            dict with at least "recipient_code", or None on failure
        """
        url = f"{PaystackService.BASE_URL}/transferrecipient"
        payload = {
            "type": "nuban",
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": "NGN",
        }
        try:
            response = requests.post(
                url,
                json=payload,
                headers=PaystackService._get_headers(),
                timeout=PaystackService.TIMEOUT,
            )
            response_data = response.json()
            if response.status_code in (200, 201) and response_data.get("status"):
                return True, response_data.get("data", {})
            logger.error(f"Paystack create_transfer_recipient failed: {response_data.get('message')}")
            return False, {"message": f"Paystack create_transfer_recipient failed: {response_data.get('message')}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Paystack transfer recipient: {e}")
            return False, {"message": f"Error creating Paystack transfer recipient: {e}"}

    @staticmethod
    def initiate_transfer(
        amount_naira: "Decimal", recipient_code: str, reference: str, reason: str, metadata: dict | None = None
    ) -> tuple[bool, dict]:
        """
        Initiate a bank transfer from the Paystack balance.

        Args:
            amount_naira:   Amount in Naira (will be converted to kobo)
            recipient_code: Paystack recipient_code from create_transfer_recipient
            reason:         Human-readable reason shown on bank statement
            reference:      Unique reference for the transfer

        Returns:
            dict with transfer data, or None on failure
        """
        url = f"{PaystackService.BASE_URL}/transfer"
        try:
            amount_kobo = int(Decimal(str(amount_naira)) * PAYSTACK_MULTIPLIER)
        except (ValueError, TypeError):
            logger.error(f"Invalid amount for Paystack transfer: {amount_naira}")
            return False, {"message": f"Invalid amount for Paystack transfer: {amount_naira}"}
        if metadata is None:
            metadata = {}
        metadata["reason"] = reason
        logger.warning(f"Initiating transfer with metadata: {metadata}")
        payload = {
            "source": "balance",
            "amount": amount_kobo,
            "recipient": recipient_code,
            "reference": reference,
            "metadata": json.dumps(metadata),
        }
        try:
            response = requests.post(
                url,
                json=payload,
                headers=PaystackService._get_headers(),
                timeout=PaystackService.TIMEOUT,
            )
            response_data = response.json()
            if response.status_code in (200, 201) and response_data.get("status"):
                return True, response_data.get("data", {})

            logger.error(f"Paystack initiate transfer failed: {response_data.get('message')}")
            return False, {"message": f"Paystack initiate transfer failed: {response_data.get('message')}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error initiating Paystack transfer: {e}")
            return False, {"message": f"Error initiating Paystack transfer: {e}"}

    @staticmethod
    def finalize_transfer(transfer_code: str, otp: str):
        """
        Completes a transfer that requires extra validation step of using OTP.
        """

        url = f"{PaystackService.BASE_URL}/transfer/finalize_transfer"

        payload = {"transfer_code": transfer_code, "otp": otp}

        try:
            response = requests.post(
                url,
                json=payload,
                headers=PaystackService._get_headers(),
                timeout=PaystackService.TIMEOUT,
            )
            response_data = response.json()
            data = response_data.get("data")

            if response.status_code == 200:
                return True, data
            logger.error(f"Paystack transfer completion failed: {response_data.get('message')}")
            message = response_data.get("message")
            return False, {"message": message}

        except requests.exceptions.RequestException as e:
            logger.error(f"Error charging Paystack payment: {e}")
            return False, {"message": str(e)}
