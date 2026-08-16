
import logging
import time

import requests
from decouple import config
from django_redis import get_redis_connection

from shared.constants.environ import DJANGO_ENV
from shared.redis.redis_service import RedisService
from shared.utils.bank_account_check import check_account_name_matches_profile

redis = get_redis_connection("default")

logger = logging.getLogger(__name__)


class FlutterwaveRecipientError(Exception):
    """Custom exception for Flutterwave recipient-related errors."""


class FlutterwaveAuth:
    """Manages the OAuth2 access token for Flutterwave API.
    
    Flutterwave provides a short-lived access token (600 seconds) for API requests. This class handles token retrieval, caching in Redis, and refreshing when necessary. The refresh process is synchronized across multiple workers using a Redis lock to prevent race conditions and ensure that only one worker refreshes the token at a time. If a worker cannot acquire the lock, it waits for another worker to populate the token in Redis before proceeding. 
    Refreshing process kicks one (1) minute before the token expires to avoid any potential issues with token expiration during API calls.
    
    If a worker holds onto the lock for too long (more than 15 seconds), it will release the lock to allow other workers to attempt token refresh. This ensures that the system remains responsive and avoids deadlocks.
    """
    
    
    FLUTTERWAVE_CLIENT_ID = config("FLUTTERWAVE_CLIENT_ID")
    FLUTTERWAVE_CLIENT_SECRET = config("FLUTTERWAVE_CLIENT_SECRET")

    TOKEN_KEY = "flutterwave:oauth:access_token"
    LOCK_KEY = "flutterwave:oauth:token_lock"

    TOKEN_URL = (
        "https://idp.flutterwave.com"
        "/realms/flutterwave/protocol/openid-connect/token"
    )

    # Flutterwave gives us 600 seconds.
    # Keep a 60-second safety margin.
    TOKEN_TTL = 540

    # How long another worker can hold the refresh lock.
    LOCK_TTL = 15

    @classmethod
    def get_access_token(cls):
        token = RedisService.get(cls.TOKEN_KEY)
        if token:
            #We're not calling `.decode` here because `RedisService.get` already returns a string.
            return token

        lock_value = RedisService.acquire_lock(cls.LOCK_KEY, cls.LOCK_TTL)
        if lock_value:
            try:
                # Double-check after acquiring the lock.
                # Another worker may have generated the token
                # just before we acquired the lock.
                token = RedisService.get(cls.TOKEN_KEY)

                if token:
                    return token

                token = cls._request_new_token()

                RedisService.set(
                    cls.TOKEN_KEY,
                    token,
                    expiry_seconds=cls.TOKEN_TTL,
                )

                return token

            finally:
                # Release the lock only if we still hold it. Employing Lua Script to ensure atomicity and avoid race conditions.
                RedisService.release_lock(cls.LOCK_KEY, lock_value)

        # Another worker is currently refreshing.
        # Wait for it to populate the token.
        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            token = RedisService.get(cls.TOKEN_KEY)

            if token:
                return token

            time.sleep(0.05)

        raise RuntimeError(
            "Unable to obtain Flutterwave access token"
        )

    @classmethod
    def _request_new_token(cls):
        response = requests.post(
            cls.TOKEN_URL,
            data={
                "client_id": cls.FLUTTERWAVE_CLIENT_ID,
                "client_secret": cls.FLUTTERWAVE_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10,
        )

        response.raise_for_status()

        data = response.json()

        token = data.get("access_token")

        if not token:
            raise RuntimeError(
                "Flutterwave did not return an access token"
            )

        return token



class FlutterwaveService:

    BASE_URL =  config("FLUTTERWAVE_BASE_URL")

    def _headers(self):
        token = FlutterwaveAuth.get_access_token()
        # if DJANGO_ENV == "development":
        #     print("<><><><><><><>TTT", token)

        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def resolve_account(self, account_number, bank_code, account_name):
        from shared.utils.bank_account_check import check_account_name_matches_profile

        payload = {
            "account": {
                "code": bank_code,
                "number": account_number,
            },
            "currency": "NGN"
        }

        response = requests.post(
            f"{self.BASE_URL}/banks/account-resolve",
            headers=self._headers(),
            json=payload,
            timeout=10,
        )

        response.raise_for_status()

        response_data = response.json()
        if response_data.get("status") == "failed":
            raise ValueError(f"Failed to resolve account on Flutterwave: {response_data.get('message')}")
        
        resolved_name = response_data.get("data", {}).get("account_name")
        if not resolved_name:
            raise ValueError("Flutterwave did not return an account name.")
        if not check_account_name_matches_profile({name.lower() for name in account_name.split()}, resolved_name):
            raise ValueError("Resolved account name does not match the provided account name.")

        return response_data

    def fetch_recipient_id(self, account_number, bank_code) -> tuple[str, str] | tuple[None, None]:
        """Attempts to fetch the recipient ID for a given bank account. If the recipient does not exist, it returns None. If the recipient exists, it returns the recipient ID."""
        cursor = None
        has_more = True

        while has_more:
            recipients, has_more, cursor = self.get_recipient_list(page_size=50, next_cursor=cursor)
            if not recipients:
                break
            for recipient in recipients:
                if (
                    recipient.get("type") == "bank"
                    and recipient.get("bank", {}).get("account_number") == account_number
                    and recipient.get("bank", {}).get("code") == bank_code
                ):
                    id = recipient.get("id")
                    name_dict = recipient.get("name", {})
                    name = " ".join(name_dict.values())
                    return id, name

            # page_number = page_number + 1

        return None, None

    def get_recipient_list(self, page_size=50, next_cursor=None) -> tuple[list, bool, str | None]:
        """Retrieves a paginated list of transfer recipients from Flutterwave. Returns a tuple containing the list of
        recipients and a boolean indicating if there are more pages to fetch.
        """
        response = requests.get(
            f"{self.BASE_URL}/transfers/recipients?size={page_size}" + (f"&next={next_cursor}" if next_cursor else ""),
            headers=self._headers(),
        )

        response.raise_for_status()
        response_data = response.json()
        if response_data.get("status") != "success":
            msg = f"Flutterwave business logic failed: {response_data.get('message')}"
            logger.warning(msg)
            raise FlutterwaveRecipientError(msg)

        data_container = response_data.get("data", [])
        cursor = data_container.get("cursor", {})
        recipients = data_container.get("recipients", [])

        has_more = cursor.get("has_more_items", False)
        return recipients, has_more, cursor.get("next")

    def get_recipient_id(self, account_number, bank_code, account_name):
        payload = {
            "type": "bank_ngn",
            "bank": {"account_number": account_number, "code": bank_code},
        }

        response = requests.post(
            f"{self.BASE_URL}/transfers/recipients",
            headers=self._headers(),
            json=payload,
            timeout=10,
        )

        resp_json = response.json()
        if response.status_code in [200, 201]:
            id_ = resp_json["data"]["id"]
            name_dict = resp_json["data"]["name"]
            name = " ".join(name_dict.values())
        elif response.status_code == 409:
            try:
                id_, name = self.fetch_recipient_id(account_number, bank_code)
            except Exception as e:
                logger.warning(f"Error fetching existing recipient ID after conflict: {e}")
                raise FlutterwaveRecipientError(f"Failed to fetch existing recipient ID after conflict: {e}")

        else:
            raise FlutterwaveRecipientError(f"Unexpected response from Flutterwave: {resp_json}")

        # Check if the resolved recipient name matches the provided account name
        if DJANGO_ENV == "production" and name is not None:
            resolved_names = {n.lower() for n in name.split()}
            if not check_account_name_matches_profile(resolved_names, account_name):
                raise FlutterwaveRecipientError("Resolved recipient name does not match the provided account name.")

        return id_
