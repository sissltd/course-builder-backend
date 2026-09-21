"""OpenAPI views with explicit cache policy for the live documentation."""

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


class NoCacheDocumentationMixin:
    """Prevent a proxy or browser from serving an incomplete old schema."""

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class DocumentationSchemaView(NoCacheDocumentationMixin, SpectacularAPIView):
    pass


class DocumentationSwaggerView(NoCacheDocumentationMixin, SpectacularSwaggerView):
    pass


class DocumentationRedocView(NoCacheDocumentationMixin, SpectacularRedocView):
    pass
