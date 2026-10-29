from rest_framework.permissions import BasePermission


class TokenEmailMatchesUser(BasePermission):
    """
    Ensures the email embedded in the JWT matches the authenticated user's email.
    Prevents a token minted for email A from being used to act as email B.
    """

    message = "Token identity does not match the authenticated user"

    def has_permission(self, request, view):
        # check if not authenticated first
        if not request.auth:
            return False

        token_email = request.auth.get("email")
        if not token_email:
            return False

        # RESOLUTION: Feature to explicitly opt out body check which then just verify the token matches the authenticated user
        if getattr(view, "skip_email_body_check", False):
            if request.user and request.user.is_authenticated:
                return token_email.lower() == request.user.email.lower()
            return False

        # If a body email is present it must match the token
        body_email = request.data.get("email", "").strip().lower()
        if body_email:
            return token_email.lower() == body_email

        # Otherwise fallback to authenticated user check
        if request.user and request.user.is_authenticated:
            return token_email.lower() == request.user.email.lower()

        return False
