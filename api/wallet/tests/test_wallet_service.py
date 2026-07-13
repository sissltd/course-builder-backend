import threading
from decimal import Decimal

from django import db
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.exceptions import ValidationError

from api.courses.tests.factories import make_user
from api.wallet.enums import TransactionStatus, TransactionType
from api.wallet.models import Wallet
from api.wallet.services import wallet_service


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
        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("50.00"))

    def test_sequential_credits_sum_correctly(self):
        user = make_user()

        wallet_service.credit_wallet(user=user, amount=Decimal("10.00"))
        wallet_service.credit_wallet(user=user, amount=Decimal("15.50"))

        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("25.50"))


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


class CreateWithdrawalRequestTests(TestCase):
    @override_settings(MINIMUM_WITHDRAWAL_THRESHOLD=Decimal("50.00"))
    def test_raises_below_minimum_threshold(self):
        user = make_user()
        wallet_service.credit_wallet(user=user, amount=Decimal("100.00"))

        with self.assertRaises(ValidationError):
            wallet_service.create_withdrawal_request(user=user, amount=Decimal("10.00"))

    @override_settings(MINIMUM_WITHDRAWAL_THRESHOLD=Decimal("50.00"))
    def test_raises_when_amount_exceeds_balance(self):
        user = make_user()
        wallet_service.credit_wallet(user=user, amount=Decimal("60.00"))

        with self.assertRaises(ValidationError):
            wallet_service.create_withdrawal_request(
                user=user, amount=Decimal("100.00")
            )

    @override_settings(MINIMUM_WITHDRAWAL_THRESHOLD=Decimal("50.00"))
    def test_happy_path_creates_pending_debit_and_decrements_balance(self):
        user = make_user()
        wallet_service.credit_wallet(user=user, amount=Decimal("100.00"))

        txn = wallet_service.create_withdrawal_request(
            user=user, amount=Decimal("60.00")
        )

        self.assertEqual(txn.type, TransactionType.DEBIT)
        self.assertEqual(txn.status, TransactionStatus.PENDING)
        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("40.00"))
