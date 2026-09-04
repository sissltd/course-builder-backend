from django.db import models
from django.utils.translation import gettext_lazy as _

from api.users.enums import KYCDocumentType, KYCStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class KYCVerification(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """An identity-verification submission (personal details -> document type
    -> ID number, per the "Complete KYC" design flow).

    One row per submission attempt rather than fields on User, since a
    rejection re-opens the flow for resubmission with new documents - the
    history of past attempts (and who reviewed them) needs to be kept.
    kyc_service.is_verified(user) looks at the latest APPROVED row and is
    what gates wallet withdrawals (see wallet.WithdrawalRequest).
    """

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="kyc_verifications",
        help_text=_("User this verification submission belongs to."),
    )
    country_of_issue = models.CharField(
        verbose_name=_("Country of Issue"),
        max_length=2,
        help_text=_("ISO 3166-1 alpha-2 code of the document's issuing country."),
    )
    document_type = models.CharField(
        verbose_name=_("Document Type"),
        max_length=32,
        choices=KYCDocumentType.choices,
        help_text=_("Type of identity document submitted."),
    )
    id_number = models.CharField(
        verbose_name=_("ID Number"),
        max_length=64,
        help_text=_("The identity document's number, as entered by the user."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=KYCStatus.choices,
        default=KYCStatus.PENDING,
        help_text=_("Review status of this submission."),
    )
    rejection_reason = models.CharField(
        verbose_name=_("Rejection Reason"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Why this submission was rejected, if applicable."),
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Reviewed By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kyc_reviews_done",
        help_text=_("Admin who approved or rejected this submission."),
    )
    reviewed_at = models.DateTimeField(
        verbose_name=_("Reviewed At"), null=True, blank=True
    )
    kyc_request_status = models.CharField(
        verbose_name=_("KYC Request Status"),
        max_length=20,
        blank=True,
        default="",
        help_text=_("Status returned by the KYC request for this submission."),
    )
    kyc_request_summary = models.JSONField(
        verbose_name=_("KYC Request Summary"),
        blank=True,
        null=True,
        help_text=_("Summary of the request sent to KYC provider for this submission."),
    )
    kyc_response_summary = models.JSONField(
        verbose_name=_("KYC Response Summary"),
        blank=True,
        null=True,
        help_text=_("Summary of the response received from the KYC provider for this submission."),
    )
    kyc_provider = models.CharField(
        verbose_name=_("KYC Provider"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("The KYC provider used for this submission."),
    )

    class Meta:
        verbose_name = _("KYC Verification")
        verbose_name_plural = _("KYC Verifications")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["user", "-created_datetime"], name="kyc_user_dt_idx"),
        ]

    def __str__(self):
        """Summarize the submission for admin/debugging readability."""

        return f"KYCVerification({self.user_id}, {self.status})"
