from decimal import Decimal
from typing import Any, cast
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from api.courses.enums import CourseSourceType, CourseStatus
from api.courses.tests.factories import make_draft_course, make_user
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.payments.services.transaction_services import (
	InsufficientFundsError,
	TransferRecipientError,
	create_transaction,
	effect_course_payment,
	get_paystack_recipient_code,
	get_wallet_for_update,
	internal_transfer,
	list_transactions,
)
from api.payments.tasks import release_course_payment
from api.users.enums import UserRole
from api.users.models.user import User
from api.wallet.models import Wallet

release_task: Any = release_course_payment


class TransactionServiceTests(TestCase):
	def setUp(self):
		self.creator = make_user(role=UserRole.COURSE_CREATOR)
		self.wallet = Wallet.objects.create(
			user=self.creator, balance=Decimal("100.00")
		)
		self.wallet_type_id = ContentType.objects.get_for_model(Wallet).id

	def test_list_transactions_returns_wallet_history_and_empty_for_missing_wallet(self):
		create_transaction(
			wallet_id=self.wallet.id,
			wallet_type_id=self.wallet_type_id,
			amount=Decimal("10.00"),
			type=Transaction.TransactionType.CREDIT,
			reference="credit-1",
			status=Transaction.TransactionStatus.COMPLETED,
		)

		transactions = list_transactions(user=cast(User, self.creator))
		self.assertEqual(transactions.count(), 1)
		first_transaction = transactions.first()
		self.assertIsNotNone(first_transaction)
		self.assertEqual(first_transaction.reference, "credit-1")
		self.assertEqual(list_transactions(user=cast(User, make_user())).count(), 0)

	def test_get_wallet_for_update_returns_the_content_object(self):
		result = get_wallet_for_update(self.wallet_type_id, cast(Any, self.wallet.id))

		self.assertEqual(result, self.wallet)

	def test_create_transaction_updates_balance_and_snapshots_details(self):
		transaction = create_transaction(
			wallet_id=self.wallet.id,
			wallet_type_id=self.wallet_type_id,
			amount=Decimal("25.00"),
			type=Transaction.TransactionType.DEBIT,
			reference="debit-1",
			status=Transaction.TransactionStatus.PENDING,
			fee=Decimal("2.50"),
			description="Withdrawal",
			recipient_account_name="Test User",
			recipient_account_number="1234567890",
			recipient_provider_name="Test Bank",
		)

		self.wallet.refresh_from_db()
		self.assertEqual(self.wallet.balance, Decimal("75.00"))
		self.assertEqual(transaction.fee, Decimal("2.50"))
		self.assertEqual(transaction.description, "Withdrawal")
		self.assertEqual(transaction.recipient_account_name, "Test User")
		self.assertEqual(transaction.recipient_account_number, "1234567890")
		self.assertEqual(transaction.recipient_provider_name, "Test Bank")

	def test_create_transaction_refuses_to_overdraw_and_rolls_back(self):
		with self.assertRaises(InsufficientFundsError):
			create_transaction(
				wallet_id=self.wallet.id,
				wallet_type_id=self.wallet_type_id,
				amount=Decimal("100.01"),
				type=Transaction.TransactionType.DEBIT,
				reference="overdrawn",
				status=Transaction.TransactionStatus.COMPLETED,
			)

		self.wallet.refresh_from_db()
		self.assertEqual(self.wallet.balance, Decimal("100.00"))
		self.assertFalse(Transaction.objects.filter(reference="overdrawn").exists())

	def test_internal_transfer_moves_money_and_creates_matching_entries(self):
		debit_ledger, _ = InternalAccount.objects.get_or_create(
			code_name="course_payment",
			defaults={"name": "Course payments", "currency": "NGN"},
		)

		internal_transfer(
			amount=Decimal("40.00"),
			from_ledger=debit_ledger,
			to_ledger=self.wallet,
			reference="course-payment-1",
			description="Course payout",
		)

		debit_ledger.refresh_from_db()
		self.wallet.refresh_from_db()
		self.assertEqual(debit_ledger.balance, Decimal("-40.00"))
		self.assertEqual(self.wallet.balance, Decimal("140.00"))
		self.assertEqual(
			Transaction.objects.filter(reference="course-payment-1").count(), 2
		)

	def test_internal_transfer_rolls_back_when_credit_fails(self):
		debit_ledger, _ = InternalAccount.objects.get_or_create(
			code_name="course_payment",
			defaults={"name": "Course payments", "currency": "NGN"},
		)

		with patch(
			"api.payments.services.transaction_services.create_transaction",
			side_effect=[Mock(), RuntimeError("credit failed")],
		), self.assertRaisesRegex(RuntimeError, "credit failed"):
			internal_transfer(
				amount=Decimal("40.00"),
				from_ledger=debit_ledger,
				to_ledger=self.wallet,
				reference="failed-transfer",
				description="Course payout",
			)

		self.assertFalse(
			Transaction.objects.filter(reference="failed-transfer").exists()
		)

	@patch("api.payments.services.transaction_services.PaystackService")
	def test_get_paystack_recipient_code_returns_code(self, paystack_service):
		paystack_service.create_transfer_recipient.return_value = (
			True,
			{"recipient_code": "RCP_test"},
		)

		result = get_paystack_recipient_code("123", "001", "Test User")

		self.assertEqual(result, "RCP_test")
		paystack_service.create_transfer_recipient.assert_called_once_with(
			account_number="123", bank_code="001", name="Test User"
		)

	@patch("api.payments.services.transaction_services.PaystackService")
	def test_get_paystack_recipient_code_normalizes_gateway_errors(self, paystack_service):
		for response in [
			(False, {"message": "Invalid account"}),
			(True, {}),
		]:
			with self.subTest(response=response):
				paystack_service.create_transfer_recipient.return_value = response
				with self.assertRaises(TransferRecipientError):
					get_paystack_recipient_code("123", "001", "Test User")

		paystack_service.create_transfer_recipient.side_effect = RuntimeError(
			"gateway unavailable"
		)
		with self.assertRaisesRegex(TransferRecipientError, "gateway unavailable"):
			get_paystack_recipient_code("123", "001", "Test User")

	@patch("api.payments.services.transaction_services.release_course_payment")
	@patch("api.payments.services.transaction_services.get_settings")
	def test_effect_course_payment_schedules_only_creator_uploaded_courses(
		self, get_settings, release_task
	):
		course = make_draft_course(
			creator=self.creator, source_type=CourseSourceType.CREATOR_UPLOADED
		)
		get_settings.return_value.auto_credit_duration_hours = 48

		effect_course_payment(course)

		release_task.apply_async.assert_called_once_with(
			args=[course.id], countdown=48 * 3600
		)

		release_task.apply_async.reset_mock()
		course.source_type = CourseSourceType.DOCUMENT_IMPORTED
		course.save(update_fields=["source_type"])
		effect_course_payment(course)
		release_task.apply_async.assert_not_called()


class ReleaseCoursePaymentTaskTests(TestCase):
	def setUp(self):
		self.creator = make_user(role=UserRole.COURSE_CREATOR)
		self.course = make_draft_course(
			creator=self.creator,
			status=CourseStatus.APPROVED,
			creator_price_snapshot=Decimal("75.00"),
		)

	def test_missing_course_is_ignored(self):
		release_task.run("00000000-0000-0000-0000-000000000000")

		self.assertEqual(Transaction.objects.count(), 0)

	def test_unapproved_course_is_ignored(self):
		self.course.status = CourseStatus.DRAFT
		self.course.save(update_fields=["status"])

		release_task.run(self.course.id)

		self.assertEqual(Transaction.objects.count(), 0)

	def test_existing_reference_is_idempotent(self):
		Transaction.objects.create(
			reference=f"course_payment_{self.course.id}",
			amount=Decimal("75.00"),
			type=Transaction.TransactionType.CREDIT,
			status=Transaction.TransactionStatus.COMPLETED,
		)

		release_task.run(self.course.id)

		self.assertEqual(Transaction.objects.count(), 1)

	@patch("api.payments.tasks.Notification.emit_in_app_notification")
	@patch("api.payments.services.transaction_services.internal_transfer")
	def test_approved_course_transfers_payment_and_notifies_creator(
		self, internal_transfer, emit_notification
	):
		debit_ledger, _ = InternalAccount.objects.get_or_create(
			code_name="course_payment",
			defaults={"name": "Course payments", "currency": "NGN"},
		)
		credit_wallet = Wallet.objects.create(user=self.creator)

		with patch(
			"api.payments.tasks.wallet_service.get_or_create_wallet",
			return_value=credit_wallet,
		):
			release_task.run(self.course.id)

		internal_transfer.assert_called_once_with(
			amount=Decimal("75.00"),
			from_ledger=debit_ledger,
			to_ledger=credit_wallet,
			reference=f"course_payment_{self.course.id}",
			description=f"Course '{self.course.title}' approved after QA verification",
			course_id=self.course.id,
		)
		emit_notification.assert_called_once_with(
			receivers=[self.creator],
			title="Course approved",
			content=f"Your course '{self.course.title}' passed QA verification and has been approved.",
			metadata={"course_id": self.course.id, "amount": Decimal("75.00")},
		)

	def test_missing_internal_account_is_not_retried(self):
		with patch(
			"api.payments.tasks.InternalAccount.objects.get",
			side_effect=InternalAccount.DoesNotExist,
		), self.assertRaises(InternalAccount.DoesNotExist):
			release_task.run(self.course.id)

	@patch("api.payments.tasks.wallet_service.get_or_create_wallet")
	@patch("api.payments.services.transaction_services.internal_transfer")
	def test_unexpected_transfer_failure_uses_task_retry(
		self, internal_transfer, get_or_create_wallet
	):
		InternalAccount.objects.get_or_create(
			code_name="course_payment",
			defaults={"name": "Course payments", "currency": "NGN"},
		)
		get_or_create_wallet.return_value = Wallet.objects.create(user=self.creator)
		internal_transfer.side_effect = RuntimeError("database unavailable")

		with patch.object(release_course_payment, "retry", side_effect=Retry) as retry, self.assertRaises(Retry):
			release_task.run(self.course.id)

		retry.assert_called_once()

	@patch("api.payments.tasks.Notification.emit_in_app_notification")
	@patch("api.payments.services.transaction_services.internal_transfer")
	def test_notification_failure_does_not_retry_payment(
		self, internal_transfer, emit_notification
	):
		InternalAccount.objects.get_or_create(
			code_name="course_payment",
			defaults={"name": "Course payments", "currency": "NGN"},
		)
		internal_transfer.return_value = None
		emit_notification.side_effect = RuntimeError("notification unavailable")

		release_task.run(self.course.id)

		internal_transfer.assert_called_once()
		emit_notification.assert_called_once()
