from collections import OrderedDict

from django.core.validators import URLValidator
from rest_framework import serializers

from argus.auth.models import User
from . import fields
from .models import (
    Acknowledgement,
    Event,
    Incident,
    IncidentTagRelation,
    SourceSystem,
    SourceSystemType,
    Tag,
)


class RemovableFieldSerializer(serializers.ModelSerializer):
    NO_PKS_KEY = "no_pks"

    def to_representation(self, instance):
        obj_repr = super().to_representation(instance)

        if self.NO_PKS_KEY in self.context:
            obj_repr.pop("pk")
        return obj_repr


class SourceSystemTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceSystemType
        fields = ["name"]


class SourceSystemSerializer(RemovableFieldSerializer):
    type = SourceSystemTypeSerializer(read_only=True)

    class Meta:
        model = SourceSystem
        fields = ["pk", "name", "type", "user"]
        read_only_fields = ["type", "user"]


class IncidentTagRelationSerializer(RemovableFieldSerializer):
    tag = serializers.CharField(write_only=True)

    class Meta:
        model = IncidentTagRelation
        fields = ["tag", "added_by", "added_time"]
        read_only_fields = ["added_by", "added_time"]

    def validate_tag(self, value: str):
        split_tag = Tag.split(value)
        if len(split_tag) < 2:
            raise serializers.ValidationError(f"The tag must contain an equality sign ({Tag.TAG_DELIMITER}) delimiter.")

        key, value_ = split_tag
        key = key.strip()
        if not key:  # Django doesn't attempt validating empty values
            raise serializers.ValidationError("The tag's key must not be empty")
        Tag._meta.get_field("key").run_validators(key)
        # Reassemble tag, to enforce key without leading or trailing whitespace (by calling `strip()` above)
        return Tag.join(key, value_)

    def create(self, validated_data: dict):
        tag = validated_data.pop("tag")
        key, value = Tag.split(tag)
        return Tag.objects.create(key=key, value=value, **validated_data)

    def to_internal_value(self, data):
        tag_dict = super().to_internal_value(data)
        if "tag" in tag_dict:
            key, value = Tag.split(tag_dict.pop("tag"))
            tag_dict["key"] = key
            tag_dict["value"] = value
        return tag_dict

    def to_representation(self, instance: IncidentTagRelation):
        tag_repr = super().to_representation(instance)
        tag_repr["tag"] = instance.tag.representation
        return tag_repr


class IncidentSerializer(RemovableFieldSerializer):
    end_time = fields.DateTimeInfinitySerializerField(required=False, allow_null=True)
    source = SourceSystemSerializer(read_only=True)
    tags = IncidentTagRelationSerializer(many=True, write_only=True)

    class Meta:
        model = Incident
        fields = [
            "pk",
            "start_time",
            "end_time",
            "source",
            "source_incident_id",
            "details_url",
            "description",
            "ticket_url",
            "tags",
        ]

    def create(self, validated_data: dict):
        assert "user" in validated_data
        assert "source" in validated_data
        user = validated_data.pop("user")

        tags_data = validated_data.pop("tags")
        tags = {Tag.objects.get_or_create(**tag_data)[0] for tag_data in tags_data}

        incident = Incident.objects.create(**validated_data)
        for tag in tags:
            IncidentTagRelation.objects.create(tag=tag, incident=incident, added_by=user)

        return incident

    def update(self, *args, **kwargs):
        """
        Use `IncidentPureDeserializer` instead.
        """
        raise NotImplementedError()

    def to_representation(self, instance: Incident):
        incident_repr = super().to_representation(instance)

        tags_field: IncidentTagRelationSerializer = self.get_fields()["tags"]
        incident_repr["tags"] = tags_field.to_representation(instance.incident_tag_relations.all())

        incident_repr["stateful"] = instance.stateful
        incident_repr["active"] = instance.active
        incident_repr["acked"] = instance.acked
        return incident_repr

    def validate_ticket_url(self, value):
        validator = URLValidator()
        validator(value)
        return value


class IncidentPureDeserializer(serializers.ModelSerializer):
    tags = IncidentTagRelationSerializer(many=True, write_only=True)

    class Meta:
        model = Incident
        fields = [
            "tags",
            "details_url",
            "ticket_url",
        ]

    def update(self, instance: Incident, validated_data: dict):
        assert "user" in validated_data
        user: User = validated_data["user"]

        tags_data = validated_data.pop("tags", [])
        posted_tags = {Tag.objects.get_or_create(**tag_data)[0] for tag_data in tags_data}

        existing_tag_relations = instance.incident_tag_relations.select_related("tag")
        existing_tags = {tag_relation.tag for tag_relation in existing_tag_relations}
        remove_tag_relations = [
            tag_relation for tag_relation in existing_tag_relations if tag_relation.tag not in posted_tags
        ]
        add_tags = posted_tags - existing_tags

        if not user.is_superuser:
            errors = {}
            for tag_relation in remove_tag_relations:
                if tag_relation.added_by != user:
                    errors[str(tag_relation.tag)] = "Cannot remove this tag when you're not the one who added it."
            if errors:
                raise serializers.ValidationError(errors)

        for tag_relation in remove_tag_relations:
            tag_relation.delete()
            # XXX: remove tag object as well if no incident is connected to it?

        for tag in add_tags:
            IncidentTagRelation.objects.create(tag=tag, incident=instance, added_by=user)

        return super().update(instance, validated_data)

    def to_representation(self, instance: Incident):
        return IncidentSerializer(instance).data

    def validate_empty_values(self, data):
        allowed_fields = self.get_fields()
        all_fields = {field.name for field in Incident._meta.get_fields()}
        all_fields.add("pk")  # for providing feedback (the default "pk" field is acually named "id")
        errors = {}
        for field in data:
            if field not in allowed_fields:
                if field in all_fields:
                    error_message = "The field is not allowed to be changed."
                else:
                    error_message = "The field does not exist."
                errors[field] = error_message
        if errors:
            raise serializers.ValidationError(errors)

        return super().validate_empty_values(data)

    def validate_ticket_url(self, value):
        return IncidentSerializer().validate_ticket_url(value)


# TODO: remove once it's not in use anymore
class IncidentSerializer_legacy(RemovableFieldSerializer):
    source = SourceSystemSerializer(read_only=True)

    class Meta:
        model = Incident
        fields = [
            "pk",
            "start_time",
            "end_time",
            "source",
            "source_incident_id",
            "details_url",
            "description",
            "ticket_url",
        ]


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            "pk",
            "incident",
            "actor",
            "timestamp",
            "type",
            "description",
        ]
        read_only_fields = ["incident", "actor"]

    def update(self, *args, **kwargs):
        """
        Events should not be changed.
        """
        raise NotImplementedError()

    def to_representation(self, instance: Event):
        event_repr = super().to_representation(instance)

        type_tuples = [
            ("value", instance.type),
            ("display", instance.get_type_display()),
        ]
        event_repr["type"] = OrderedDict(type_tuples)
        return event_repr


class AcknowledgementSerializer(serializers.ModelSerializer):
    event = EventSerializer()

    class Meta:
        model = Acknowledgement
        fields = [
            "pk",
            "event",
            "expiration",
        ]
        # "pk" needs to be listed, as "event" is the actual primary key
        read_only_fields = ["pk"]

    def create(self, validated_data: dict):
        assert "incident" in validated_data
        assert "actor" in validated_data
        incident = validated_data.pop("incident")
        actor = validated_data.pop("actor")

        event_data = validated_data.pop("event")
        event = Event.objects.create(incident=incident, actor=actor, **event_data)
        return Acknowledgement.objects.create(event=event, **validated_data)

    def to_internal_value(self, data: dict):
        if "type" not in data["event"]:
            data["event"]["type"] = Event.Type.ACKNOWLEDGE
        return super().to_internal_value(data)

    def validate_event(self, value: dict):
        event_type = value["type"]
        if event_type != Event.Type.ACKNOWLEDGE:
            raise serializers.ValidationError(
                f"'{event_type}' is not a valid event type for acknowledgements."
                f" Use '{Event.Type.ACKNOWLEDGE}' or omit 'type' completely."
            )
        return value

    def validate(self, attrs: dict):
        expiration = attrs.get("expiration")
        if expiration and expiration <= attrs["event"]["timestamp"]:
            raise serializers.ValidationError("'expiration' must be after 'event.timestamp'.")
        return attrs
