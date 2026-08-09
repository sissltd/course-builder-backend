from rest_framework import serializers

from api.authentication.enums import TokenPurpose

#: Only these two purposes are ever resent through this public, unauthenticated
#: endpoint. TokenPurpose also includes STAFF_INVITATION, WITHDRAWAL_CONFIRMATION,
#: and EMAIL_CHANGE - each of those is issued and resent through its own
#: authenticated/authorized flow, and must never be reachable here: accepting
#: them would let anyone who merely knows a target's email address invalidate
#: (via token_service.issue_token's one-active-token-per-purpose invalidation)
#: a pending staff invitation or an in-flight withdrawal OTP for that person.
RESENDABLE_TOKEN_PURPOSES = (
    TokenPurpose.SIGNUP_VERIFICATION,
    TokenPurpose.PASSWORD_RESET,
)


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(
        choices=[(p.value, p.label) for p in RESENDABLE_TOKEN_PURPOSES]
    )
