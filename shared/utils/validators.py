import re

from rest_framework import serializers


def validate_nigerian_account_number(value: str) -> str:
    """
    Validate Nigerian bank account number (NUBAN format).

    Nigerian bank account numbers must:
    - Be exactly 10 digits
    - Contain only numeric characters

    Args:
        value: The account number to validate

    Returns:
        The validated account number

    Raises:
        serializers.ValidationError: If validation fails
    """
    if not value:
        raise serializers.ValidationError("Account number is required.")

    # Remove any whitespace
    value = value.strip()

    # Check if it contains only digits
    if not value.isdigit():
        raise serializers.ValidationError("Account number must contain only digits.")

    # Check length (Nigerian NUBAN is exactly 10 digits)
    if len(value) != 10:
        raise serializers.ValidationError("Account number must be exactly 10 digits.")

    return value


def validate_phone_number(value: str) -> str:
    """
    Validate Nigerian phone number format.

    Accepts formats:
    - 08012345678 (11 digits starting with 0)
    - 2348012345678 (13 digits starting with 234)
    - +2348012345678 (with plus prefix)

    Args:
        value: The phone number to validate

    Returns:
        The validated phone number

    Raises:
        serializers.ValidationError: If validation fails
    """
    if not value:
        return value

    # Remove spaces, dashes, and parentheses
    cleaned = re.sub(r"[\s\-\(\)]", "", value)

    # Remove leading + if present
    cleaned = cleaned.removeprefix("+")

    # Check if it contains only digits after cleaning
    if not cleaned.isdigit():
        raise serializers.ValidationError("Phone number must contain only digits.")

    # Valid formats:
    # 08012345678 (11 digits starting with 0)
    # 2348012345678 (13 digits starting with 234)
    if (
        len(cleaned) == 11
        and cleaned.startswith("0")
        or len(cleaned) == 13
        and cleaned.startswith("234")
    ):
        return value
    elif len(cleaned) == 10 and not cleaned.startswith("0"):
        # Some systems strip the leading 0
        return value
    else:
        raise serializers.ValidationError(
            "Invalid phone number format. Use format: 08012345678 or 2348012345678"
        )
