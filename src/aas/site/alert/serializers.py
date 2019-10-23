from rest_framework import serializers

from .models import Alert, NetworkSystem, Object, ParentObject, ProblemType


class NetworkSystemSerializer(serializers.ModelSerializer):
    class Meta:
        model = NetworkSystem
        fields = ['name', 'type']

    type = serializers.StringRelatedField()


class ObjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Object
        fields = ['name', 'object_id', 'url', 'type']

    type = serializers.StringRelatedField()


class ParentObjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParentObject
        fields = ['name', 'parentobject_id', 'url']


class ProblemTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProblemType
        fields = ['name', 'description']


class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = ['pk', 'timestamp', 'source', 'alert_id', 'object', 'parent_object', 'details_url', 'problem_type', 'description']
        read_only_fields = ['pk']

    # TODO: make these return a normal dict instead of an OrderedDict
    # source = NetworkSystemSerializer(read_only=True)
    # object = ObjectSerializer(read_only=True)
    # parent_object = ParentObjectSerializer(read_only=True)
    # problem_type = ProblemTypeSerializer(read_only=True)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['source'] = NetworkSystemSerializer(instance.source).data
        representation['object'] = ObjectSerializer(instance.object).data
        representation['parent_object'] = ParentObjectSerializer(instance.parent_object).data
        representation['problem_type'] = ProblemTypeSerializer(instance.problem_type).data
        return representation
