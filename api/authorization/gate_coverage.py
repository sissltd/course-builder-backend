"""Which endpoints are gated by a stored permission, and which are not."""

from rest_framework.test import APIRequestFactory

from api.authorization.gate_matrix import iter_gates
from api.authorization.permissions import HasPermission


def _uses_permission(permission) -> bool:
    """True if a permission instance, or anything it composes, is a HasPermission."""

    if isinstance(permission, HasPermission):
        return True
    for attr in ("op1", "op2"):
        operand = getattr(permission, attr, None)
        if operand is not None and _uses_permission(operand):
            return True
    return False


def ungated_handlers() -> set[str]:
    """`ViewClass.action` for every handler whose gate checks no stored permission."""

    factory = APIRequestFactory()
    ungated = set()
    for _key, view_class, initkwargs, method, action in iter_gates():
        view = view_class(**initkwargs)
        view.args, view.kwargs, view.format_kwarg = (), {}, None
        if action is not None:
            view.action_map, view.action = {method: action}, action
        view.request = view.initialize_request(getattr(factory, method)("/"))
        if not any(_uses_permission(p) for p in view.get_permissions()):
            ungated.add(f"{view_class.__name__}.{action or method}")
    return ungated
