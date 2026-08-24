from rest_framework.permissions import BasePermission

from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount


class IsMieDeveloper(BasePermission):
    """Gate every developer-facing mie view.

    Authentication stores the resolved DeveloperAccount as request.auth
    and has already rejected non-active accounts; this is the second half
    of the pair - it fails closed if any future auth path forgets the
    status check, because a request without an APPROVED account behind it
    never passes.
    """

    message = "Active approved developer credentials are required."

    def has_permission(self, request, view) -> bool:
        developer = getattr(request, "auth", None)
        return (
            isinstance(developer, DeveloperAccount)
            and developer.status == DeveloperAccountStatus.APPROVED
        )
