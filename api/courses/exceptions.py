from rest_framework import exceptions, status


class ModuleLocked(exceptions.APIException):
    """Raised when a module edit is attempted while another user holds an
    active edit lock on it (SCCS PRD Section 14). Flows through the
    existing drf_standardized_errors pipeline unchanged - only the
    status_code differs from ValidationError/PermissionDenied."""

    status_code = status.HTTP_423_LOCKED
    default_detail = "This module is currently being edited by another user."
    default_code = "locked"
