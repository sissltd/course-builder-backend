from rest_framework.renderers import BaseRenderer


class ServerSentEventsRenderer(BaseRenderer):
    """Expose text/event-stream during DRF content negotiation.

    StreamingHttpResponse writes its own chunks, so render() is only relevant
    if a DRF Response accidentally reaches this renderer.
    """

    media_type = "text/event-stream"
    format = "sse"
    charset = "utf-8"
    render_style = "text"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, bytes):
            return data
        return str(data).encode(self.charset)
