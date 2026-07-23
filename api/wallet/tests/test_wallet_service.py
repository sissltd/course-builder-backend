import re
import threading
from decimal import Decimal

from django import db
from django.core import mail
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.exceptions import ValidationError

from api.courses.tests.factories import make_user
from api.users.enums import KYCStatus
from api.users.models import KYCVerification
from api.wallet.enums import TransactionStatus, TransactionType, WithdrawalRequestStatus
from api.wallet.models import Wallet
from api.wallet.services import wallet_service


def _approve_kyc(user):
    KYCVerification.objects.create(
        user=user,
        country_of_issue="NG",
        document_type="NATIONAL_ID",
        id_number="12345",
        status=KYCStatus.APPROVED,
    )


class GetOrCreateWalletTests(TestCase):
    def test_creates_zero_balance_wallet_once_and_is_idempotent(self):
        user = make_user()

        wallet1 = wallet_service.get_or_create_wallet(user=user)
        wallet2 = wallet_service.get_or_create_wallet(user=user)

        self.assertEqual(wallet1.id, wallet2.id)
        self.assertEqual(wallet1.balance, Decimal("0.00"))
        self.assertEqual(Wallet.objects.filter(user=user).count(), 1)


class CreditWalletTests(TestCase):
    def test_increases_balance_and_creates_completed_credit_transaction(self):
        user = make_user()

        txn = wallet_service.credit_wallet(
            user=user, amount=Decimal("50.00"), description="test"
        )

        self.assertEqual(txn.type, TransactionType.CREDIT)
        self.assertEqual(txn.status, TransactionStatus.COMPLETED)
        self.assertTrue(txn.reference.startswith("TXN-"))
        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("50.00"))

    def test_sequential_credits_sum_correctly(self):
        user = make_user()

        wallet_service.credit_wallet(user=user, amount=Decimal("10.00"))
        wallet_service.credit_wallet(user=user, amount=Decimal("15.50"))

        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("25.50"))


class GetWalletTotalsTests(TestCase):
    def test_sums_completed_credits_and_pending_debits_separately(self):
        user = make_user()
        _approve_kyc(user)
        wallet_service.credit_wallet(user=user, amount=Decimal("100.00"))
        payout_account = wallet_service.create_payout_account(
            user=user,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )
        withdrawal_request = wallet_service.request_withdrawal(
            user=user, amount=Decimal("60.00"), payout_account_id=payout_account.id
        )
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        wallet_service.confirm_withdrawal(
            user=user,
            withdrawal_request_id=withdrawal_request.id,
            code=code,
        )

        wallet = wallet_service.get_or_create_wallet(user=user)
        totals = wallet_service.get_wallet_totals(wallet=wallet)
        self.assertEqual(totals["total_earned"], Decimal("100.00"))
        self.assertEqual(totals["pending_balance"], Decimal("60.00"))


class ConcurrentCreditWalletTests(TransactionTestCase):
    """Uses TransactionTestCase (no outer test-wrapping transaction) with real
    threads/connections so select_for_update()'s row locking is actually
    exercised, proving concurrent credits cannot produce a lost update."""

    def test_concurrent_credits_do_not_lose_updates(self):
        user = make_user()
        wallet_service.get_or_create_wallet(
            user=user
        )  # pre-provision to avoid a get_or_create race

        barrier = threading.Barrier(2)
        errors = []

        def credit():
            try:
                barrier.wait(timeout=5)
                wallet_service.credit_wallet(user=user, amount=Decimal("10.00"))
            except (
                Exception
            ) as exc:  # pragma: no cover - failure path surfaced via errors list
                errors.append(exc)
            finally:
                db.connections.close_all()

        threads = [threading.Thread(target=credit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("20.00"))


class PayoutAccountTests(TestCase):
    def test_first_account_becomes_default_automatically(self):
        user = make_user()

        account = wallet_service.create_payout_account(
            user=user,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )

        self.assertTrue(account.is_default)

    def test_second_default_account_demotes_the_first(self):
        user = make_user()
        first = wallet_service.create_payout_account(
            user=user,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )

        second = wallet_service.create_payout_account(
            user=user,
            account_type="MOBILE_MONEY",
            provider_name="MTN",
            account_number="0987654321",
            account_name="Test User",
            is_default=True,
        )

        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)


class RequestWithdrawalTests(TestCase):
    def setUp(self):
        self.user = make_user()
        wallet_service.credit_wallet(user=self.user, amount=Decimal("100.00"))
        self.payout_account = wallet_service.create_payout_account(
            user=self.user,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )

    def test_raises_when_kyc_not_verified(self):
        with self.assertRaises(ValidationError):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("60.00"),
                payout_account_id=self.payout_account.id,
            )

    @override_settings(MINIMUM_WITHDRAWAL_THRESHOLD=Decimal("50.00"))
    def test_raises_below_minimum_threshold(self):
        _approve_kyc(self.user)

        with self.assertRaises(ValidationError):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("10.00"),
                payout_account_id=self.payout_account.id,
            )

    @override_settings(MINIMUM_WITHDRAWAL_THRESHOLD=Decimal("50.00"))
    def test_raises_when_amount_exceeds_balance(self):
        _approve_kyc(self.user)

        with self.assertRaises(ValidationError):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("500.00"),
                payout_account_id=self.payout_account.id,
            )

    @override_settings(MINIMUM_WITHDRAWAL_THRESHOLD=Decimal("50.00"))
    def test_happy_path_creates_pending_request_without_touching_balance(self):
        _approve_kyc(self.user)

        withdrawal_request = wallet_service.request_withdrawal(
            user=self.user,
            amount=Decimal("60.00"),
            payout_account_id=self.payout_account.id,
        )

        self.assertEqual(
            withdrawal_request.status, WithdrawalRequestStatus.PENDING_CONFIRMATION
        )
        wallet = wallet_service.get_or_create_wallet(user=self.user)
        self.assertEqual(wallet.balance, Decimal("100.00"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("60.00", mail.outbox[0].body)


class ConfirmWithdrawalTests(TestCase):
    def setUp(self):
        self.user = make_user()
        _approve_kyc(self.user)
        wallet_service.credit_wallet(user=self.user, amount=Decimal("100.00"))
        self.payout_account = wallet_service.create_payout_account(
            user=self.user,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )
        self.withdrawal_request = wallet_service.request_withdrawal(
            user=self.user,
            amount=Decimal("60.00"),
            payout_account_id=self.payout_account.id,
        )
        self.code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def test_wrong_code_rejected_and_balance_untouched(self):
        with self.assertRaises(Exception):
            wallet_service.confirm_withdrawal(
                user=self.user,
                withdrawal_request_id=self.withdrawal_request.id,
                code="000000",
            )
        wallet = wallet_service.get_or_create_wallet(user=self.user)
        self.assertEqual(wallet.balance, Decimal("100.00"))

    def test_happy_path_creates_debit_transaction_and_decrements_balance(self):
        txn = wallet_service.confirm_withdrawal(
            user=self.user,
            withdrawal_request_id=self.withdrawal_request.id,
            code=self.code,
        )

        self.assertEqual(txn.type, TransactionType.DEBIT)
        self.assertEqual(txn.status, TransactionStatus.PENDING)
        self.assertEqual(txn.recipient_account_number, "1234567890")
        wallet = wallet_service.get_or_create_wallet(user=self.user)
        self.assertEqual(wallet.balance, Decimal("40.00"))

        self.withdrawal_request.refresh_from_db()
        self.assertEqual(
            self.withdrawal_request.status, WithdrawalRequestStatus.CONFIRMED
        )
        self.assertEqual(self.withdrawal_request.transaction_id, txn.id)

    def test_cannot_confirm_twice(self):
        wallet_service.confirm_withdrawal(
            user=self.user,
            withdrawal_request_id=self.withdrawal_request.id,
            code=self.code,
        )

        with self.assertRaises(Exception):
            wallet_service.confirm_withdrawal(
                user=self.user,
                withdrawal_request_id=self.withdrawal_request.id,
                code=self.code,
            )
