from rest_framework import serializers


class GlobalSearchItemSerializer(serializers.Serializer):
    type = serializers.CharField(
        help_text="Resource type, such as course, user, category, or topic."
    )
    id = serializers.UUIDField(help_text="Resource identifier.")
    title = serializers.CharField(help_text="Primary text to display.")
    subtitle = serializers.CharField(
        allow_blank=True,
        help_text="Secondary text to display beneath the title.",
    )
    status = serializers.CharField(
        allow_blank=True,
        help_text="Resource lifecycle/status value when the resource has one.",
    )
    api_path = serializers.CharField(
        help_text="API path the frontend can use to fetch or list this resource."
    )


class GlobalSearchBucketSerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text="Number of results in this bucket.")
    results = GlobalSearchItemSerializer(many=True)


class GlobalSearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField(help_text="Normalized search query.")
    limit = serializers.IntegerField(help_text="Maximum results returned per bucket.")
    total_count = serializers.IntegerField(help_text="Total results across buckets.")
    results = serializers.DictField(help_text="Search results keyed by bucket name.")
