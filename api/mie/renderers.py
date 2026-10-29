from rest_framework.renderers import BaseRenderer


class PdfRenderer(BaseRenderer):
    """Lets content negotiation accept ``Accept: application/pdf``.

    DRF negotiates content in ``APIView.initial()`` - before the handler
    runs - and raises 406 when no renderer matches the client's Accept
    header. The documentation download declares ``application/pdf`` in
    its schema, so Swagger UI asks for exactly that; without a renderer
    advertising the type, every request from Swagger was rejected before
    reaching the view.

    Nothing is actually rendered through here: the view returns a plain
    ``HttpResponse``, which DRF passes through untouched. ``render`` only
    guards the edge case of a DRF ``Response`` reaching it, which would
    mean a bug rather than a normal path.
    """

    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data if isinstance(data, bytes) else b""
