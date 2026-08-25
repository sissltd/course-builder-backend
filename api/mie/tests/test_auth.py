from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

import jwt as pyjwt
from django.conf import settings
from django.test import TestCase
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.enums import DeveloperAccountStatus
from api.mie.permissions import IsMieDeveloper
from api.mie.services import dev_token_service, key_service as key_service_module
from api.mie.services.key_service import (
    ApiKeyRejected,
    authenticate_key,
    generate_raw_key,
    hash_raw_key,
    issue_credentials,
    revoke_key,
)
from api.mie.tests.factories import (
    make_approved_account,
    make_approved_developer,
    make_developer_account,
)


def _assert_dev_token_code(testcase, callable_, expected_code):
    with testcase.assertRaises(dev_token_service.DevTokenInvalid) as ctx:
        callable_()
    testcase.assertEqual(ctx.exception.code, expected_code)


class KeyLifecycleTests(TestCase):
    """Key material can only exist on APPROVED/SUSPENDED rows (DB
    constraint), so every lifecycle test starts from an approved account -
    exactly the state approval leaves behind."""

    def test_issued_key_verifies_and_stamps_last_used(self):
        account = make_approved_account()
        raw = issue_credentials(account)

        resolved = authenticate_key(raw)

        self.assertEqual(resolved.id, account.id)
        account.refresh_from_db()
        self.assertIsNotNone(account.api_key_last_used_at)
        self.assertEqual(account.api_key_prefix, raw[:16])
        self.assertEqual(account.api_key_hash, hash_raw_key(raw))

    def test_raw_key_is_never_persisted(self):
        account = make_approved_account()
        raw = issue_credentials(account)

        stored_fields = str(list(account.__dict__.values()))
        self.assertNotIn(raw, stored_fields)

    def test_rotation_invalidates_previous_key(self):
        account = make_approved_account()
        old_raw = issue_credentials(account)
        new_raw = issue_credentials(account)

        with self.assertRaises(ApiKeyRejected) as ctx:
            authenticate_key(old_raw)
        self.assertEqual(ctx.exception.code, "invalid_api_key")

        self.assertEqual(authenticate_key(new_raw).id, account.id)

    def test_revoke_disables_authentication(self):
        account = make_approved_account()
        raw = issue_credentials(account)
        revoke_key(account)

        with self.assertRaises(ApiKeyRejected):
            authenticate_key(raw)

    def test_tampered_key_rejected(self):
        account = make_approved_account()
        raw = issue_credentials(account)

        tampered = raw[:-1] + ("A" if raw[-1] != "A" else "B")
        with self.assertRaises(ApiKeyRejected):
            authenticate_key(tampered)

    def test_wrong_prefix_rejected_without_lookup(self):
        with self.assertRaises(ApiKeyRejected) as ctx:
            authenticate_key("other_service_deadbeef")
        self.assertEqual(ctx.exception.code, "invalid_api_key")

    def test_status_gates_via_enforcer(self):
        """PENDING/REJECTED accounts can never carry key material (DB
        constraint), so their rejection paths are asserted on the shared
        enforcer directly; SUSPENDED is exercised end-to-end below."""
        cases = {
            DeveloperAccountStatus.PENDING: "account_not_active",
            DeveloperAccountStatus.REJECTED: "account_not_active",
            DeveloperAccountStatus.SUSPENDED: "account_suspended",
        }
        for status, expected_code in cases.items():
            with self.subTest(status=status):
                account = make_developer_account(status=status)

                with self.assertRaises(ApiKeyRejected) as ctx:
                    key_service_module._enforce_active_status(account)
                self.assertEqual(ctx.exception.code, expected_code)

    def test_suspended_account_with_key_rejected_end_to_end(self):
        account = make_developer_account(status=DeveloperAccountStatus.SUSPENDED)
        raw = issue_credentials(account)

        with self.assertRaises(ApiKeyRejected) as ctx:
            authenticate_key(raw)
        self.assertEqual(ctx.exception.code, "account_suspended")


class PlatformDevTokenTests(TestCase):
    def test_round_trip_resolves_account(self):
        account, _raw_key = make_approved_developer()

        token = dev_token_service.issue_dev_token(account)

        self.assertEqual(dev_token_service.resolve_dev_token(token).id, account.id)

    def test_expired_token_rejected_with_distinct_code(self):
        account, _raw_key = make_approved_developer()

        with mock.patch.object(
            dev_token_service, "_access_lifetime", return_value=timedelta(seconds=-10)
        ):
            token = dev_token_service.issue_dev_token(account)

        _assert_dev_token_code(
            self,
            lambda: dev_token_service.resolve_dev_token(token),
            "token_expired",
        )

    def test_non_mie_tokens_rejected_even_if_signature_valid(self):
        now = datetime.now(dt_timezone.utc)
        foreign = pyjwt.encode(
            {
                "user_id": 1,
                "typ": "access",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            settings.SIMPLE_JWT["SIGNING_KEY"],
            algorithm="HS256",
        )

        _assert_dev_token_code(
            self,
            lambda: dev_token_service.resolve_dev_token(foreign),
            "invalid_token",
        )

    def test_suspension_kills_live_session_immediately(self):
        account, _raw_key = make_approved_developer()
        token = dev_token_service.issue_dev_token(account)

        account.status = DeveloperAccountStatus.SUSPENDED
        account.save(update_fields=["status", "updated_datetime"])

        _assert_dev_token_code(
            self,
            lambda: dev_token_service.resolve_dev_token(token),
            "account_not_active",
        )

    def test_deleted_account_rejected(self):
        account, _raw_key = make_approved_developer()
        token = dev_token_service.issue_dev_token(account)
        account.delete()

        _assert_dev_token_code(
            self,
            lambda: dev_token_service.resolve_dev_token(token),
            "account_not_found",
        )


class AuthenticationBackendTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.backend = MieDeveloperAuthentication()

    def _request(self, **headers):
        return self.factory.get("/api/v1/mie/v1/me/", **headers)

    def test_api_key_header_sets_principal_and_method(self):
        account, _seed_raw = make_approved_developer()
        raw = issue_credentials(account)
        request = self._request(HTTP_X_MIE_API_KEY=raw)

        _user, auth = self.backend.authenticate(request)

        self.assertIsNone(_user)
        self.assertEqual(auth.id, account.id)
        self.assertEqual(request.mie_auth_method, "api_key")

    def test_bearer_platform_token_sets_principal_and_method(self):
        account, _raw_key = make_approved_developer()
        token = dev_token_service.issue_dev_token(account)
        request = self._request(HTTP_AUTHORIZATION=f"Bearer {token}")

        _user, auth = self.backend.authenticate(request)

        self.assertIsNone(_user)
        self.assertEqual(auth.id, account.id)
        self.assertEqual(request.mie_auth_method, "platform")

    def test_no_credentials_raises_with_code(self):
        with self.assertRaises(AuthenticationFailed) as ctx:
            self.backend.authenticate(self._request())
        self.assertEqual(ctx.exception.get_codes(), "no_credentials")

    def test_api_key_failure_surfaces_machine_code(self):
        _, raw_key_of_other = make_approved_developer()

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.backend.authenticate(self._request(HTTP_X_MIE_API_KEY=generate_raw_key()))
        self.assertEqual(ctx.exception.get_codes(), "invalid_api_key")

    def test_authenticate_header_present_for_401s(self):
        self.assertTrue(self.backend.authenticate_header(self._request()))


class PermissionTests(TestCase):
    def setUp(self):
        self.permission = IsMieDeveloper()

    def _request_with(self, developer):
        class _Request:
            auth = developer
            user = None

        return _Request()

    def test_approved_principal_passes(self):
        approved, _raw_key = make_approved_developer()

        self.assertTrue(self.permission.has_permission(self._request_with(approved), None))

    def test_non_approved_principal_fails_closed(self):
        pending = make_developer_account()

        self.assertFalse(self.permission.has_permission(self._request_with(pending), None))

    def test_missing_principal_fails_closed(self):
        self.assertFalse(self.permission.has_permission(self._request_with(None), None))

    def test_foreign_auth_object_fails_closed(self):
        class _NotAnAccount:
            status = "APPROVED"

        self.assertFalse(
            self.permission.has_permission(self._request_with(_NotAnAccount()), None)
        )
