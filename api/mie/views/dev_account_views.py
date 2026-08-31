from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.renderers import PdfRenderer
from api.mie.serializers.dev_me_serializer import DeveloperMeSerializer
from api.mie.services import documentation_pdf_service, documentation_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

DOCUMENTATION_SECTIONS = (
    "meta, api, your_account, quickstart, integration_flow, authentication, "
    "reference_scheme, submission_lifecycle, deduplication, plan_and_payouts, "
    "endpoints, webhooks, errors, rate_limits, pagination, go_live_checklist, faq"
)


@extend_schema(tags=["Developer — MIE Account"])
class MieDeveloperMeView(APIView):
    """The authenticated developer's own account snapshot."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Show your account",
        description=(
            "Return the authenticated developer's account details: current "
            "lifecycle status, payout plan, webhook URL, masked API key "
            "preview, the signing secret used to verify inbound webhooks, "
            "and key usage timestamps.\n\n"

            "Call this to verify credentials are working or to retrieve the "
            "signing secret before building a webhook receiver. A 200 "
            "response doubles as a credentials health-check.\n\n"

            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"

            "**Prerequisites:** The developer account must be APPROVED.\n\n"

            "**Important:** The full API key is shown exactly once, in the "
            "admin approval response, and only its SHA-256 hash is stored — "
            "this endpoint always returns a masked preview. The signing "
            "secret IS returned in full: it only verifies our messages to "
            "you and cannot authenticate requests on your behalf. A "
            "PENDING, REJECTED, or SUSPENDED account gets 401."
        ),
        responses={
            200: OpenApiResponse(
                response=DeveloperMeSerializer,
                description="Authenticated developer's account snapshot.",
                examples=[
                    OpenApiExample(
                        "Account snapshot",
                        value={
                            "email": "dev@studio.io",
                            "status": "APPROVED",
                            "plan_type": "PAID_PER_SUBMISSION",
                            "webhook_url": "https://hooks.studio.io/mie",
                            "api_key_preview": "scb_live_a1b2c3d...",
                            "api_key_last_used_at": "2026-08-24T15:30:00Z",
                            "signing_secret": "s3Cr3t-43-char-url-safe-signing-secret-value",
                            "created_datetime": "2026-08-20T10:00:00Z",
                            "decided_at": "2026-08-21T09:00:00Z",
                        },
                        response_only=True,
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(DeveloperMeSerializer(request.auth).data)


@extend_schema(tags=["Developer — MIE Account"])
class MieDocumentationView(APIView):
    """Machine-readable integration documentation for this developer."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Integration documentation (JSON)",
        description=(
            "Return the complete, end-to-end integration reference for this "
            "developer as a single JSON document — everything needed to "
            "build, verify, and operate an MIE integration without "
            "contacting support.\n\n"

            f"**Sections:** {DOCUMENTATION_SECTIONS}.\n\n"

            "That covers: the caller's live account state; a five-step "
            "quickstart with copy-pasteable curl; every stage a submission "
            "passes through from registration to payout, naming the actor "
            "and the webhook fired at each; the credential model and every "
            "authentication failure code; the SCB-xxxxxxxx-S reference "
            "scheme and why the suffix must not be used as a key; each "
            "submission status with what sets it, whether it is terminal, "
            "and what to do about it; the three ordered deduplication "
            "checks; payout semantics for the caller's plan; every "
            "endpoint with request/response examples, query parameters, "
            "and error cases; the full webhook contract including HMAC "
            "verification code in Python and Node, the retry schedule, and "
            "a sample signed body per event type; the error envelope and "
            "status catalogue; live rate limits; the pagination envelope; "
            "a go-live checklist; and an FAQ.\n\n"

            "Every value is generated from live server constants — enum "
            "members, the reference-suffix map, the dispatcher's retry "
            "table, the throttle rates in settings — so it cannot drift "
            "from what the API actually does. Safe to fetch at build time "
            "to generate client constants.\n\n"

            "For a formatted PDF of the same content, call "
            "`GET /api/v1/mie/v1/documentation/download/`.\n\n"

            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"

            "**Prerequisites:** The developer account must be APPROVED.\n\n"

            "**Important:** Read-only and idempotent apart from the "
            "`meta.generated_at` timestamp and the personalised "
            "`your_account` section."
        ),
        responses={
            200: OpenApiResponse(
                description=(
                    "The full integration reference. Top-level keys: "
                    f"{DOCUMENTATION_SECTIONS}."
                ),
                examples=[
                    OpenApiExample(
                        "Documentation payload (abridged)",
                        value={
                            "meta": {
                                "document": "MIE developer integration reference",
                                "documentation_version": "2.0.0",
                                "generated_at": "2026-08-31T10:00:00+00:00",
                            },
                            "api": {
                                "base_url": "https://api.example.com/api/v1",
                                "content_type": "application/json",
                            },
                            "your_account": {
                                "email": "dev@studio.io",
                                "status": "APPROVED",
                                "plan_type": "PAID_PER_SUBMISSION",
                                "api_key_preview": "scb_live_a1b2c3d...",
                            },
                            "authentication": {
                                "primary": {
                                    "type": "API key",
                                    "header": "X-MIE-Api-Key",
                                    "key_format": (
                                        "'scb_live_' followed by 43 url-safe "
                                        "base64 characters (256 bits of entropy)."
                                    ),
                                },
                            },
                            "reference_scheme": {
                                "format": "SCB-<8 hex chars>-<status letter>",
                                "example": "SCB-0d1c7b2e-P",
                            },
                            "webhooks": {
                                "verification": {
                                    "algorithm": "HMAC-SHA256",
                                    "signed_string": (
                                        "'{X-MIE-Timestamp}.{raw request body bytes}'"
                                    ),
                                    "replay_window_seconds": 300,
                                },
                                "events": [
                                    {
                                        "type": "SUBMISSION_APPROVED",
                                        "sample_body": {
                                            "event_id": "8f14e45f-ceea-4e78-9a1b-2c3d4e5f6a7b",
                                            "type": "SUBMISSION_APPROVED",
                                            "occurred_at": "2026-08-24T15:30:00.123456+00:00",
                                            "submission": {
                                                "reference": "SCB-0d1c7b2e-A",
                                                "status": "APPROVED",
                                                "title": "Build a Production-Grade Rust Course",
                                            },
                                        },
                                    }
                                ],
                            },
                        },
                        response_only=True,
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(
            documentation_service.build_documentation(request.auth, request=request)
        )


@extend_schema(tags=["Developer — MIE Account"])
class MieDocumentationDownloadView(APIView):
    """The same integration documentation, rendered as a PDF."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    # PdfRenderer first so `Accept: application/pdf` (what Swagger sends,
    # reading the schema below) and `Accept: */*` both negotiate; JSON is
    # kept so a client asking for it still gets readable error bodies.
    renderer_classes = [PdfRenderer, JSONRenderer]

    def finalize_response(self, request, response, *args, **kwargs):
        """Render error bodies as JSON whatever the client asked for.

        Content negotiation happens before authentication, so a request
        for application/pdf that then 401s would otherwise try to render
        the error envelope through PdfRenderer and emit an unreadable
        body. The swap has to happen on the *request*: DRF's own
        finalize_response copies request.accepted_renderer onto the
        response, overwriting anything set here directly.
        """

        if isinstance(response, Response) and response.status_code >= 400:
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = "application/json"
        return super().finalize_response(request, response, *args, **kwargs)

    @extend_schema(
        summary="Integration documentation (PDF download)",
        description=(
            "Return the complete integration reference as a formatted PDF "
            "attachment — a title page, contents, and one typeset section "
            "per topic, with tables for the enum and status catalogues and "
            "monospaced blocks for every curl command, code sample, and "
            "JSON body.\n\n"

            "Call this when the JSON document is impractical to read: "
            "circulating the integration spec inside your team, reviewing "
            "it away from Swagger, or keeping a dated copy of the contract "
            "you built against.\n\n"

            "The content is rendered from the exact payload "
            "`GET /api/v1/mie/v1/documentation/` returns, so the two can "
            "never disagree.\n\n"

            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"

            "**Prerequisites:** The developer account must be APPROVED.\n\n"

            "**Important:** Responds with `application/pdf` and a "
            "`Content-Disposition: attachment` header, not JSON. The "
            "filename is derived from the account email. Swagger UI shows "
            "a download link rather than rendering the body."
        ),
        responses={
            # (status, media_type) keys the response under application/pdf
            # instead of the view's default JSON renderer, so Swagger UI
            # offers a download rather than trying to render bytes.
            (200, "application/pdf"): OpenApiResponse(
                response=OpenApiTypes.BINARY,
                description=(
                    "PDF attachment containing the full integration "
                    "reference."
                ),
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        account = request.auth
        documentation = documentation_service.build_documentation(
            account, request=request
        )
        pdf = documentation_pdf_service.build_documentation_pdf(
            documentation, account=account
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{documentation_pdf_service.filename_for(account)}"'
        )
        response["Content-Length"] = str(len(pdf))
        return response
