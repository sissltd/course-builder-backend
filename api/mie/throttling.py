"""Rate limiting for the MIE developer API.

MIE has two kinds of caller, and each is limited by what identifies it:

* Authenticated developer routes are limited per developer account -
  whichever credential it presents, API key or platform session. The
  account is what is being limited, so its bucket follows it across IPs
  and is never shared with another account behind the same IP.
* The public registration route has no account yet, so it stays on DRF's
  ScopedRateThrottle and is limited per client IP.

ScopedRateThrottle cannot do the first on its own: it keys on
request.user, and MIE authentication deliberately leaves that anonymous
(developers are not platform users) and puts the account on
request.auth. Every developer route therefore fell back to the IP.
"""

from rest_framework.throttling import ScopedRateThrottle

from api.mie.models import DeveloperAccount


class MieDeveloperRateThrottle(ScopedRateThrottle):
    """ScopedRateThrottle keyed on the authenticated DeveloperAccount."""

    def get_cache_key(self, request, view):
        developer = getattr(request, "auth", None)
        if not isinstance(developer, DeveloperAccount):
            # Throttles run after authentication and permissions, so these
            # requests are already refused; there is no account to key on.
            return None
        return self.cache_format % {"scope": self.scope, "ident": developer.pk}
