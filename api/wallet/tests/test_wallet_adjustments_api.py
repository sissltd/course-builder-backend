"""Issue Refund: admin credits and debits to a creator's wallet."""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.payments.services.transaction_services import (
    InsufficientFundsError,
    create_transaction,
)
from api.users.enums import UserRole
from api.users.models import UserActivityLog
from api.wallet.models import Wallet, WalletAdjustment

LIST_URL = "/api/v1/admin/wallet-adjustments/"


def adjust_url(wallet):
    return f"/api/v1/admin/wallets/{wallet.id}/adjustments/"


class WalletAdjustmentTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(role=UserRole.SUPER_ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.wallet = Wallet.objects.create(user=self.creator)
        InternalAccount.objects.get_or_create(
            code_name="adjustments",
            defaults={"name": "Admin Adjustments", "currency": "NGN"},
        )
        self.client.force_authenticate(self.super_admin)

    def adjust(
        self, direction="CREDIT", amount="5000.00", key="key-1", reason="Missed payout"
    ):
        return self.client.post(
            adjust_url(self.wallet),
            {
                "direction": direction,
                "amount": amount,
                "reason": reason,
                "idempotency_key": key,
            },
            format="json",
        )

    def test_credit_moves_money_double_entry(self):
        response = self.adjust()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))
        adjustments = InternalAccount.objects.get(code_name="adjustments")
        self.assertEqual(adjustments.balance, Decimal("-5000.00"))
        reference = response.data["data"]["reference"]
        self.assertEqual(Transaction.objects.filter(reference=reference).count(), 2)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.creator, action="WALLET_ADJUSTED"
            ).exists()
        )
        self.assertTrue(Notification.objects.filter(receiver=self.creator).exists())

    def test_debit_cannot_overdraw(self):
        self.adjust(amount="100.00", key="credit")

        refused = self.adjust(direction="DEBIT", amount="150.00", key="too-much")
        allowed = self.adjust(direction="DEBIT", amount="40.00", key="ok")

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(allowed.status_code, status.HTTP_201_CREATED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("60.00"))

    def test_retry_with_the_same_key_moves_money_once(self):
        first = self.adjust()
        retry = self.adjust()
        clash = self.adjust(amount="1.00")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry.status_code, status.HTTP_200_OK)
        self.assertEqual(retry.data["data"]["id"], first.data["data"]["id"])
        self.assertEqual(clash.status_code, status.HTTP_409_CONFLICT)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))

    def test_validation(self):
        for payload in ({"amount": "0"}, {"direction": "SIDEWAYS"}, {"reason": ""}):
            with self.subTest(payload=payload):
                body = {
                    "direction": "CREDIT",
                    "amount": "1.00",
                    "reason": "x",
                    "idempotency_key": "v",
                }
                body.update(payload)
                response = self.client.post(
                    adjust_url(self.wallet), body, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_earning_roles_wallets(self):
        reviewer_wallet = Wallet.objects.create(
            user=make_user(role=UserRole.CREATOR_REVIEWER)
        )

        response = self.client.post(
            adjust_url(reviewer_wallet),
            {
                "direction": "CREDIT",
                "amount": "1.00",
                "reason": "x",
                "idempotency_key": "r",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_without_issue_refund_is_refused_but_can_list(self):
        self.adjust()
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        self.assertEqual(
            self.adjust(key="admin").status_code, status.HTTP_403_FORBIDDEN
        )
        listing = self.client.get(
            LIST_URL, {"user_id": str(self.creator.id), "direction": "CREDIT"}
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data["data"]["paginator"]["count"], 1)
        self.assertEqual(
            self.client.get(LIST_URL, {"direction": "DEBIT"}).data["data"]["paginator"][
                "count"
            ],
            0,
        )

    def test_creators_cannot_list(self):
        self.client.force_authenticate(self.creator)

        self.assertEqual(
            self.client.get(LIST_URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_list_query_count_does_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(LIST_URL)
            return len(ctx.captured_queries)

        self.adjust(key="k0")
        one = count()
        for n in range(1, 5):
            self.adjust(key=f"k{n}", amount="1.00")
        self.assertEqual(count(), one)


class NegativeBalanceGuardTests(TestCase):
    def test_a_wallet_debit_below_zero_is_refused_under_the_lock(self):
        wallet = Wallet.objects.create(user=make_user(), balance=Decimal("10.00"))

        with self.assertRaises(InsufficientFundsError):
            create_transaction(
                wallet_id=wallet.id,
                wallet_type_id=ContentType.objects.get_for_model(Wallet).id,
                amount=Decimal("10.01"),
                type=Transaction.TransactionType.DEBIT,
                reference="REF",
                status=Transaction.TransactionStatus.COMPLETED,
            )

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("10.00"))
        self.assertFalse(WalletAdjustment.objects.exists())
