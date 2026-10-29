from rest_framework import serializers

from api.support.enums import (
    SupportRequestKind,
    SupportRequestStatus,
    SupportTicketPriority,
)
from api.support.models import SupportRequest, SupportTicketMessage
from api.support.services import support_service


class SupportRequestSerializer(serializers.ModelSerializer):
    """Read representation shared by every Support endpoint."""

    submitted_by_email = serializers.EmailField(
        source="submitted_by.email", read_only=True, default=None
    )
    submitted_by_name = serializers.CharField(
        source="submitted_by.get_full_name", read_only=True, default=None
    )
    assigned_to_name = serializers.CharField(
        source="assigned_to.get_full_name", read_only=True, default=None
    )

    class Meta:
        model = SupportRequest
        fields = [
            "id",
            "kind",
            "first_name",
            "last_name",
            "email",
            "country",
            "title",
            "web_link",
            "message",
            "status",
            "priority",
            "due_at",
            "resolution_notes",
            "resolved_at",
            "submitted_by_email",
            "submitted_by_name",
            "assigned_to_name",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields


class SupportContactCreateSerializer(serializers.Serializer):
    """Figma 'Contact us' form: first/last name, email, country, message."""

    first_name = serializers.CharField(
        max_length=150, help_text="Sender's first name, up to 150 characters."
    )
    last_name = serializers.CharField(
        max_length=150, help_text="Sender's last name, up to 150 characters."
    )
    email = serializers.EmailField(
        help_text="Address Support replies to; not checked against any account."
    )
    country = serializers.RegexField(
        r"^[A-Za-z]{2}$",
        error_messages={"invalid": "Use a two-letter ISO country code."},
        help_text="ISO 3166-1 alpha-2 code, e.g. `NG`; stored upper-cased.",
    )
    message = serializers.CharField(help_text="The question or problem, free text.")

    def validate_country(self, value: str) -> str:
        return value.upper()

    def create(self, validated_data):
        user = self.context["request"].user
        return support_service.submit_contact(
            user=user if user.is_authenticated else None, **validated_data
        )

    def to_representation(self, instance):
        return SupportRequestSerializer(instance, context=self.context).data


class SupportTitledRequestCreateSerializer(serializers.Serializer):
    """Figma 'Request for an appeal' form: Title*, Email*, Web link,
    Description*. The view supplies `kind`."""

    title = serializers.CharField(
        max_length=255, help_text="Short headline for the appeal, up to 255 characters."
    )
    email = serializers.EmailField(
        help_text="Address Support contacts about this appeal; may differ from the account email."
    )
    web_link = serializers.URLField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional supporting link as a full URL (include `https://`). Omit or send empty when there is none.",
    )
    description = serializers.CharField(
        help_text="What was rejected or suspended and why it should be reconsidered."
    )

    def create(self, validated_data):
        return support_service.submit_titled_request(
            user=self.context["request"].user,
            kind=self.context["kind"],
            **validated_data,
        )

    def to_representation(self, instance):
        return SupportRequestSerializer(instance, context=self.context).data


class SupportTicketCreateSerializer(serializers.Serializer):
    """Figma 'Submit a ticket' form: Subject*, Email*, Priority*,
    Description*. Unlike the appeal form it has no web link. `subject` is
    stored as the request's `title`."""

    subject = serializers.CharField(
        max_length=255,
        help_text="Ticket subject, up to 255 characters. Returned as `title`.",
    )
    email = serializers.EmailField(
        help_text="Address the confirmation and progress emails go to; may differ from the account email."
    )
    priority = serializers.ChoiceField(
        choices=SupportTicketPriority.choices,
        help_text="`LOW`, `MEDIUM` or `HIGH`.",
    )
    description = serializers.CharField(
        help_text="The problem in detail. Returned as `message` and shown as the thread's opening bubble."
    )

    def create(self, validated_data):
        return support_service.submit_titled_request(
            user=self.context["request"].user,
            kind=SupportRequestKind.TICKET,
            title=validated_data.pop("subject"),
            **validated_data,
        )

    def to_representation(self, instance):
        return SupportRequestSerializer(instance, context=self.context).data


class SupportTicketMessageSerializer(serializers.ModelSerializer):
    """One thread entry: a reply bubble or a 'status changed to' line.
    `from_support` is true when anyone other than the submitter wrote it;
    `is_mine` is true when the requesting user wrote it (Figma 'You')."""

    author_id = serializers.UUIDField(
        read_only=True,
        allow_null=True,
        help_text="User who wrote the entry; null if that account was deleted.",
    )
    author_name = serializers.CharField(
        source="author.get_full_name", read_only=True, default=None
    )
    from_support = serializers.SerializerMethodField(
        help_text="True when anyone other than the ticket's submitter wrote it."
    )
    is_mine = serializers.SerializerMethodField(
        help_text="True when the caller wrote it — label the bubble 'You'."
    )

    class Meta:
        model = SupportTicketMessage
        fields = [
            "id",
            "kind",
            "body",
            "from_status",
            "to_status",
            "author_id",
            "author_name",
            "from_support",
            "is_mine",
            "created_datetime",
        ]
        read_only_fields = fields

    def get_from_support(self, obj: SupportTicketMessage) -> bool:
        return obj.author_id != obj.ticket.submitted_by_id

    def get_is_mine(self, obj: SupportTicketMessage) -> bool:
        request = self.context.get("request")
        return bool(request and obj.author_id and obj.author_id == request.user.id)


class SupportTicketSubmitterSerializer(serializers.Serializer):
    """Ticket Overview 'Created By' block."""

    id = serializers.UUIDField()
    name = serializers.CharField(source="get_full_name")
    email = serializers.EmailField()
    role = serializers.CharField(source="get_role_display")


class SupportTicketDetailSerializer(SupportRequestSerializer):
    """Ticket detail: the shared fields plus the thread and submitter."""

    submitted_by = SupportTicketSubmitterSerializer(read_only=True, allow_null=True)
    messages = SupportTicketMessageSerializer(many=True, read_only=True)

    class Meta(SupportRequestSerializer.Meta):
        fields = SupportRequestSerializer.Meta.fields + ["submitted_by", "messages"]
        read_only_fields = fields


class SupportTicketReplySerializer(serializers.Serializer):
    body = serializers.CharField(
        help_text="The reply text. Attachments are not supported."
    )


#: Statuses an admin can set; OPEN is only ever the starting state.
_SETTABLE_STATUSES = [
    choice
    for choice in SupportRequestStatus.choices
    if choice[0] != SupportRequestStatus.OPEN
]


class SupportTicketStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=_SETTABLE_STATUSES,
        help_text="`IN_PROGRESS`, `RESOLVED` or `CLOSED`. `OPEN` cannot be set.",
    )


class SupportTicketBulkStatusSerializer(SupportTicketStatusSerializer):
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=100,
        help_text="1–100 ticket IDs from the checked rows.",
    )


class SupportTicketBulkSkipSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    reason = serializers.CharField()


class SupportTicketBulkResultSerializer(serializers.Serializer):
    updated = SupportRequestSerializer(many=True)
    skipped = SupportTicketBulkSkipSerializer(many=True)


class SupportTicketStatusCountsSerializer(serializers.Serializer):
    open = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    resolved = serializers.IntegerField()
    closed = serializers.IntegerField()


class SupportResolveSerializer(serializers.Serializer):
    """Body of the staff resolve action - optional free-text notes."""

    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional resolution notes stored on the request. Omit for none.",
    )
