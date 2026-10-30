from shared.services.storage_service import StorageService


class DurableMediaInputMixin:
    """Stores the serializer's `media_fields` in their durable form.

    A client may send the presigned `file_url` from the upload response; it
    expires after minutes, so it is stored as the object's stable URL instead.
    """

    media_fields: tuple[str, ...] = ()

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        for field in self.media_fields:
            if values.get(field):
                values[field] = StorageService.durable_media_ref(values[field])
        return values


class PlayableMediaOutputMixin:
    """Serves the serializer's `media_fields` as URLs a browser can open now.

    The bucket is private, so a stored reference to our own object is signed
    on every read; links to other hosts pass through unchanged.
    """

    media_fields: tuple[str, ...] = ()

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        for field in self.media_fields:
            if representation.get(field):
                representation[field] = StorageService.playable_url(
                    representation[field]
                )
        return representation


class MediaFieldsMixin(DurableMediaInputMixin, PlayableMediaOutputMixin):
    """Durable on write, playable on read."""
