"""
Admin serializer for the SISSLConfiguration singleton — used by the
admin/config/ GET + PATCH endpoints to tune thresholds without a redeploy.

Notes:
  - `singleton_key` is intentionally excluded — it's an internal guard
    field and must not be editable from the API.
  - There is NO verification_mode here. Verification is automatic; if SISSL
    fails the user does not proceed. Do not add a bypass mode.
"""

from rest_framework import serializers
from api.sissl_verification.models import SISSLConfiguration


# >>>>>>>>>>>>>>>>>>>>>>> Config Serializer <<<<<<<<<<<<<<<<<<<<<<<<<<
class SISSLConfigurationSerializer(serializers.ModelSerializer):
    """
    GET + PATCH on the singleton.

    All threshold fields are validated as 0 - 100 at the model layer (via
    Min/MaxValueValidators) so we don't repeat that here.
    """

    class Meta:
        model = SISSLConfiguration
        fields = [
            "id",
            "face_match_threshold",
            "flagging_floor",
            "liveness_threshold",
            "retry_max_attempts",
            "http_timeout_seconds",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = ["id", "created_datetime", "updated_datetime"]

    def validate(self, attrs):
        """
        Cross-field sanity: the flagging floor should never sit ABOVE the
        face-match threshold — that would mean "flag if better than the
        passing score", which is incoherent.

        We compose against `self.instance` for PATCH (partial updates) so
        a caller that only sends one of the two still gets a coherent check.
        """
        face = attrs.get(
            "face_match_threshold",
            getattr(self.instance, "face_match_threshold", None),
        )
        floor = attrs.get(
            "flagging_floor",
            getattr(self.instance, "flagging_floor", None),
        )

        if face is not None and floor is not None and floor > face:
            raise serializers.ValidationError(
                {"flagging_floor": "Flagging floor must be at most the face match threshold."}
            )

        return attrs
