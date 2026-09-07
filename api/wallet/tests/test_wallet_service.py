import re
import threading
from decimal import Decimal
from unittest.mock import patch

from django import db
from django.core import mail
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from api.courses.tests.factories import make_user
from api.payments.models.transaction_model import Transaction
from api.platform.enums import PaymentProcessors
from api.platform.models import PlatformSettings
from api.users.enums import KYCStatus, UserRole
from api.users.models import KYCVerification
from api.wallet.enums import TransactionStatus, TransactionType, WithdrawalRequestStatus
from api.wallet.models import Wallet
from api.wallet.services import wallet_service
from core.models import TransferOutboxEvent
from shared.utils.encryption import decrypt_field


def _approve_kyc(user):
    KYCVerification.objects.create(
        user=user,
        country_of_issue="NG",
        document_type="NATIONAL_ID",
        id_number="12345",
        status=KYCStatus.APPROVED,
        date_of_birth="1990-01-01",
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
        # self.assertTrue(txn.reference.startswith("TXN-"))
        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("50.00"))

    def test_sequential_credits_sum_correctly(self):
        user = make_user()

        wallet_service.credit_wallet(user=user, amount=Decimal("10.00"))
        wallet_service.credit_wallet(user=user, amount=Decimal("15.50"))

        wallet = wallet_service.get_or_create_wallet(user=user)
        self.assertEqual(wallet.balance, Decimal("25.50"))


class GetWalletTotalsTests(TestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

        self.flutterwave_recipient_patcher = patch(
            "shared.services.flutterwave_service.FlutterwaveService.get_recipient_id", return_value="RCP_TEST_123"
        )
        self.transfer_task_patcher = patch("api.wallet.services.wallet_service.dispatch_transfer_task.delay")

        self.processor_patcher = patch.object(PlatformSettings, "payment_processor", PaymentProcessors.FLUTTERWAVE)
        self.flutterwave_recipient_patcher.start()
        self.transfer_task_patcher.start()
        self.processor_patcher.start()

    def tearDown(self):
        self.flutterwave_recipient_patcher.stop()
        self.transfer_task_patcher.stop()
        self.processor_patcher.stop()

    def test_sums_completed_credits_and_pending_debits_separately(self):
        user = make_user()
        _approve_kyc(user)
        wallet_service.credit_wallet(user=user, amount=Decimal("100.00"))
        payout_account = wallet_service.create_payout_account(
            user=user,
            account_type="LOCAL",
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058"
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
        self.assertEqual(totals["pending_balance"], Decimal("0.00"))


class ConcurrentCreditWalletTests(TransactionTestCase):
    """Uses TransactionTestCase (no outer test-wrapping transaction) with real
    threads/connections so select_for_update()'s row locking is actually
    exercised, proving concurrent credits cannot produce a lost update."""

    # See the note on NotificationStreamApiTests: without this, the flush
    # TransactionTestCase performs would strip migration-seeded data for
    # every app tested after wallet.
    def _fixture_teardown(self):
        from core.testing import reseed_reference_data

        super()._fixture_teardown()
        reseed_reference_data()

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
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058"
        )

        self.assertTrue(account.is_default)

    def test_second_default_account_demotes_the_first(self):
        user = make_user()
        first = wallet_service.create_payout_account(
            user=user,
            account_type="LOCAL",
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058"
        )

        second = wallet_service.create_payout_account(
            user=user,
            account_type="MOBILE_MONEY",
            bank_name="MTN",
            account_number="0987654321",
            account_name="Test User",
            is_default=True,
            bank_code="058"
        )

        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_wrong_role_cannot_create_payout_account(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

        with self.assertRaises(PermissionDenied):
            wallet_service.create_payout_account(
                user=reviewer,
                account_type="LOCAL",
                bank_name="Access Bank",
                account_number="1234567890",
                account_name="Test User",
                bank_code="058"
            )


class RequestWithdrawalTests(TestCase):
    def setUp(self):
        self.user = make_user()
        wallet_service.credit_wallet(user=self.user, amount=Decimal("100.00"))
        self.payout_account = wallet_service.create_payout_account(
            user=self.user,
            account_type="LOCAL",
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058"
        )

    def test_raises_when_kyc_not_verified(self):
        with self.assertRaises(ValidationError):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("60.00"),
                payout_account_id=self.payout_account.id,
            )

    def test_wrong_role_cannot_request_withdrawal(self):
        _approve_kyc(self.user)
        self.user.role = UserRole.ADMIN
        self.user.save(update_fields=["role"])

        with self.assertRaises(PermissionDenied):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("60.00"),
                payout_account_id=self.payout_account.id,
            )

    def test_raises_below_minimum_threshold(self):
        _approve_kyc(self.user)

        with self.assertRaises(ValidationError):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("10.00"),
                payout_account_id=self.payout_account.id,
            )

    def test_raises_when_amount_exceeds_balance(self):
        _approve_kyc(self.user)

        with self.assertRaises(ValidationError):
            wallet_service.request_withdrawal(
                user=self.user,
                amount=Decimal("500.00"),
                payout_account_id=self.payout_account.id,
            )

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
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058"
        )
        self.withdrawal_request = wallet_service.request_withdrawal(
            user=self.user,
            amount=Decimal("60.00"),
            payout_account_id=self.payout_account.id,
        )
        self.code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

        # setting processor to Flutterwave and mocking the dispatch_transfer_task.delay to avoid actual task execution during tests and mocking the get_recipient_id method to return a test recipient code. This ensures that the tests can run without relying on external services and can focus on the logic of confirming withdrawals and handling wallet balances.
        self.flutterwave_recipient_patcher = patch(
            "shared.services.flutterwave_service.FlutterwaveService.get_recipient_id",
            return_value="RCP_TEST_123",
        )
        self.transfer_task_patcher = patch("api.wallet.services.wallet_service.dispatch_transfer_task.delay")
        self.processor_patcher = patch.object(PlatformSettings, "payment_processor", PaymentProcessors.FLUTTERWAVE)
        self.flutterwave_recipient_patcher.start()
        self.transfer_task_patcher.start()
        self.processor_patcher.start()

    def tearDown(self):
        self.flutterwave_recipient_patcher.stop()
        self.transfer_task_patcher.stop()
        self.processor_patcher.stop()

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
        self.assertEqual(txn.status, TransactionStatus.COMPLETED)
        self.assertEqual(decrypt_field(txn.recipient_account_number), "1234567890")
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

    def test_payout_account_with_existing_recipient_code(self):
        """When the payout account has the appropriate recipient code for the
        configured processor, the wrapped function should return it and not
        call any of the update or get_recipient_id methods.
        """

        self.payout_account.flutterwave_recipient_code = "RCP_EXISTING_123"
        self.payout_account.save(update_fields=["flutterwave_recipient_code"])

        with (
            patch(
                "api.wallet.services.wallet_service._transfer_processor_and_recipient_code",
                wraps=wallet_service._transfer_processor_and_recipient_code,
            ) as spy,
            patch("api.wallet.services.wallet_service._update_account_recipient_code") as update_recipient_code_mock,
            patch("shared.services.flutterwave_service.FlutterwaveService.get_recipient_id") as get_recipient_id_mock,
        ):
            wallet_service.confirm_withdrawal(
                user=self.user,
                withdrawal_request_id=self.withdrawal_request.id,
                code=self.code,
            )

        spy.assert_called_once_with(self.payout_account)
        # an existing recipient code means neither of these should be hit
        update_recipient_code_mock.assert_not_called()
        get_recipient_id_mock.assert_not_called()

        # confirm the wrapped (real) function itself returns a plain 2-tuple
        result = wallet_service._transfer_processor_and_recipient_code(self.payout_account)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertEqual(result, (PaymentProcessors.FLUTTERWAVE, "RCP_EXISTING_123"))

    def test_payout_account_creates_recipient_code_when_missing(self):
        """When the payout account has no recipient code for the configured processor, the wrapped function should call
        the appropriate service to create one and update the payout account with it
        """

        self.assertFalse(self.payout_account.flutterwave_recipient_code)

        with (
            patch(
                "api.wallet.services.wallet_service._update_account_recipient_code",
                wraps=wallet_service._update_account_recipient_code,
            ) as update_recipient_code_mock,
            patch(
                "shared.services.flutterwave_service.FlutterwaveService.get_recipient_id",
                return_value="RCP_NEW_123",
            ) as fltw_get_recipient_id_mock,
            patch(
                "shared.services.paystack_service.PaystackService.create_transfer_recipient"
            ) as pstck_recipient_code_mock,
        ):
            wallet_service.confirm_withdrawal(
                user=self.user,
                withdrawal_request_id=self.withdrawal_request.id,
                code=self.code,
            )

        update_recipient_code_mock.assert_called_once()  # payout acct is updated bcos it previously had no recipient code

        # The processor is Flutterwave, so the get_recipient_id method should be called to create a new recipient code,
        # while the Paystack method should not be called.
        fltw_get_recipient_id_mock.assert_called_once()
        pstck_recipient_code_mock.assert_not_called()

    def test_confirm_withdrawal(self):
        wallet_service.confirm_withdrawal(
            user=self.user,
            withdrawal_request_id=self.withdrawal_request.id,
            code=self.code,
        )
        self.withdrawal_request.refresh_from_db()
        self.assertEqual(self.withdrawal_request.status, WithdrawalRequestStatus.CONFIRMED)
        self.assertEqual(self.withdrawal_request.transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(Transaction.objects.count(), 3)  # 1 for test setup and two for the withdrawal operation
        self.assertEqual(Transaction.objects.filter(type=TransactionType.DEBIT).count(), 1)
        self.assertEqual(
            Transaction.objects.filter(type=TransactionType.CREDIT).count(), 2
        )  # One for the test setup and one for the withdrawal operation
        self.assertTrue([txn.amount == self.withdrawal_request.amount for txn in Transaction.objects.all()])
        self.assertTrue(TransferOutboxEvent.objects.filter(transfer_request=self.withdrawal_request).exists())