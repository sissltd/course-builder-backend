"""
Reference and ID generation utilities.

This module provides utility functions for generating unique reference codes,
transaction IDs, audit log IDs, and platform-specific identifiers used across
the Feexet application.
"""
import random
import secrets
import string
import time

from django.utils import timezone


def generate_reference(length=12):
    """
    Generate a random alphanumeric reference code.

    Creates a cryptographically secure random string using uppercase letters
    and digits. Suitable for transaction references, booking codes, etc.

    Args:
        length (int, optional): The desired length of the reference code.
            Defaults to 12 characters.

    Returns:
        str: A random alphanumeric string of specified length.

    Example:
        >>> ref = generate_reference()
        >>> len(ref)
        12
        >>> ref = generate_reference(length=8)
        'A7B9C2D1'

    Notes:
        - Uses secrets module for cryptographically strong randomness
        - Character set: A-Z, 0-9 (36 possible characters)
        - Collision probability is very low for reasonable lengths
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def generate_general_reference(prefix="TXN", length=16):
    """
    Generate a reference code with a custom prefix.

    Creates a reference ID with a specified prefix followed by random
    alphanumeric characters. Useful for creating categorized reference codes.

    Args:
        prefix (str, optional): The prefix to prepend to the reference.
            Defaults to "TXN".
        length (int, optional): Total length of the reference including prefix.
            Defaults to 16 characters.

    Returns:
        str: A reference code in the format: {prefix}{random_chars}

    Raises:
        ValueError: If length is less than or equal to the prefix length.

    Example:
        >>> ref = generate_general_reference(prefix="BK", length=10)
        'BKA7B9C2D1'
        >>> ref = generate_general_reference(prefix="TXN-", length=20)
        'TXN-A7B9C2D1F3E5'

    Notes:
        - Character set for random part: A-Z, 0-9
        - Total length must be greater than prefix length
        - Uses cryptographically secure randomness
    """
    random_length = length - len(prefix)

    if random_length <= 0:
        raise ValueError("Length must be greater than prefix length")

    # Generate random characters
    chars = string.ascii_uppercase + string.digits
    random_part = "".join(secrets.choice(chars) for _ in range(random_length))

    return f"{prefix}{random_part}"


def generate_audit_log_reference():
    """
    Generate a unique audit log reference ID.

    Creates an audit log ID with the "AUD-" prefix for tracking system events
    and user actions throughout the application.

    Returns:
        str: An audit log reference in the format: AUD-XXXXXX (12 chars total)

    Example:
        >>> audit_ref = generate_audit_log_reference()
        'AUD-A7B9C2'
        >>> len(audit_ref)
        12

    Notes:
        - Format: "AUD-" + 6 random alphanumeric characters
        - Used for audit trail tracking
        - Guaranteed unique with high probability
    """
    return generate_general_reference(prefix="AUD-", length=12)


def generate_platform_id_listing():
    """
    Generate a unique platform-specific listing ID.

    Creates a listing ID in the Feexeet format using the "FXT" prefix,
    a timestamp component, and a random number for uniqueness.

    Returns:
        str: A listing ID in the format: FXT{timestamp}-{random} (e.g., FXT42-567)

    Example:
        >>> listing_id = generate_platform_id_listing()
        'FXT42-567'
        >>> listing_id.startswith('FXT')
        True

    Notes:
        - Format: FXT{last_2_digits_of_timestamp}-{3_digit_random}
        - Timestamp uses modulo 100 to get last 2 digits of current time
        - Random component is between 100-999
        - Provides reasonable uniqueness for listing identification
    """
    prefix = "FXT"
    timestamp = int(time.time()) % 100
    rand = random.randint(100, 999)

    return f"{prefix}{timestamp}-{rand}"


def generate_unique_reference():
    """
    Generate a unique alphanumeric reference code. The reference is based on the current timestamp and a random component to ensure uniqueness.
    """
    separator = "z"
    now = timezone.now()
    now_str = str(now).replace(" ", separator).replace("-", "").replace(":", "").replace(".", "")
    random_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    reference_ = f"{now_str[:15]}-{random_str}"
    return reference_
