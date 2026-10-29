"""Who passes each endpoint's view-level permission gate.

Walks every /api/v1/ route and, for each (method, route, view, action) and
each principal, evaluates `view.get_permissions()` the way DRF does before a
handler runs. The result is the view-gate half of "who may call what" -
object-level checks, querysets and service re-checks are out of its reach and
have their own tests.

Used to prove that moving gates from role classes to stored permissions
changes nobody's access: the matrix computed from today's code is saved as a
baseline, and the parity test recomputes it after every conversion.
"""

from django.contrib.auth.models import AnonymousUser
from django.urls import URLResolver, get_resolver
from rest_framework.test import APIRequestFactory, force_authenticate

from api.users.enums import UserRole

API_PREFIX = "api/v1/"
HANDLER_METHODS = ("get", "post", "put", "patch", "delete")

#: Principal labels, in the order they appear in the baseline. Every role, a
#: superuser who is not the Super Admin (is_superuser bypasses role checks),
#: and an unauthenticated caller.
SUPERUSER = "SUPERUSER"
ANONYMOUS = "ANONYMOUS"
PRINCIPAL_LABELS = (*UserRole.values, SUPERUSER, ANONYMOUS)


def build_unsaved_principals() -> dict:
    """In-memory users per label, for gates that only read role attributes."""

    from api.users.models import User

    principals = {role: User(role=role) for role in UserRole.values}
    principals[SUPERUSER] = User(role=UserRole.COURSE_CREATOR, is_superuser=True)
    principals[ANONYMOUS] = AnonymousUser()
    return principals


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern), pattern.callback


def iter_gates():
    """Yield (key, view_class, initkwargs, method, action) per gated handler."""

    seen = set()
    for route, callback in _walk(get_resolver().url_patterns):
        view_class = getattr(callback, "cls", None)
        if not route.startswith(API_PREFIX) or view_class is None:
            continue
        # DRF routers also register a `.json`-style format-suffix copy of each
        # route; it resolves to the same view and gate, so it adds only noise.
        if "(?P<format>" in route:
            continue
        actions = getattr(callback, "actions", None)
        if actions:
            # DRF adds a `head` entry to a viewset's action map in place after
            # its first request, so it is excluded to keep the matrix
            # independent of what ran before it.
            handlers = sorted(
                (m, a) for m, a in actions.items() if m in HANDLER_METHODS
            )
        else:
            handlers = [(m, None) for m in HANDLER_METHODS if hasattr(view_class, m)]
        for method, action in handlers:
            key = f"{method.upper()} {route} [{view_class.__name__}.{action or method}]"
            if key in seen:
                continue
            seen.add(key)
            yield key, view_class, getattr(callback, "initkwargs", {}), method, action


def _passes(*, view_class, initkwargs, method, action, user) -> bool | str:
    factory = APIRequestFactory()
    django_request = getattr(factory, method)("/")
    if not isinstance(user, AnonymousUser):
        force_authenticate(django_request, user=user)
    view = view_class(**initkwargs)
    view.args, view.kwargs = (), {}
    view.format_kwarg = None
    if action is not None:
        view.action_map = {method: action}
        view.action = action
    request = view.initialize_request(django_request)
    view.request = request
    try:
        return all(p.has_permission(request, view) for p in view.get_permissions())
    except Exception as exc:  # noqa: BLE001 - recorded, so a crash is a visible diff
        return f"ERROR:{type(exc).__name__}"


def compute_matrix(principals: dict) -> dict[str, list[str]]:
    """{gate key: sorted principal labels that pass (or an error marker)}."""

    matrix = {}
    for key, view_class, initkwargs, method, action in iter_gates():
        allowed = []
        for label in PRINCIPAL_LABELS:
            result = _passes(
                view_class=view_class,
                initkwargs=initkwargs,
                method=method,
                action=action,
                user=principals[label],
            )
            if result is True:
                allowed.append(label)
            elif result is not False:
                allowed.append(f"{label}:{result}")
        matrix[key] = allowed
    return dict(sorted(matrix.items()))
