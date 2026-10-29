from rest_framework import serializers


class EraseAccountSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=500,
        help_text="Why the account is being deleted. Kept in the audit log.",
    )
    confirm_email = serializers.CharField(
        help_text="The account's email address, typed to confirm. Must match exactly."
    )
