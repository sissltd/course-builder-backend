from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.users.enums import UserRole
from api.wallet.services import wallet_service


class WalletApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)

    def test_creator_can_retrieve_auto_provisioned_wallet(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/wallet/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["balance"], "0.00")

    def test_non_creator_role_forbidden(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/wallet/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_forbidden(self):
        response = self.client.get("/api/v1/wallet/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_transaction_list_filter_by_type(self):
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("30.00"))
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/transactions/", {"type": "CREDIT"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_withdrawal_above_threshold_succeeds(self):
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("100.00"))
        self.client.force_authenticate(self.creator)

        response = self.client.post("/api/v1/withdrawals/", {"amount": "60.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_withdrawal_below_threshold_rejected(self):
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("100.00"))
        self.client.force_authenticate(self.creator)

        response = self.client.post("/api/v1/withdrawals/", {"amount": "5.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
