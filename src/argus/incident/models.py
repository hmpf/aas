from random import randint

from django.core import validators
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from argus.auth.models import User
from argus.site.datetime_utils import INFINITY_REPR, get_infinity_repr
from .fields import DateTimeInfinityField


def validate_lowercase(value: str):
    if not value.islower():
        raise ValidationError(f"'{value}' is not a lowercase string")


def get_or_create_default_instances():
    argus_user, _ = User.objects.get_or_create(username="argus", is_superuser=True)
    sst, _ = SourceSystemType.objects.get_or_create(name="argus")
    ss, _ = SourceSystem.objects.get_or_create(name="argus", type=sst, user=argus_user)
    return (argus_user, sst, ss)


def create_fake_incident():
    MAX_ID = 2 ** 32 - 1
    MIN_ID = 1
    argus_user, _, source_system = get_or_create_default_instances()
    incident = Incident.objects.create(
        start_time=timezone.now(),
        end_time="infinity",
        source_incident_id=randint(MIN_ID, MAX_ID),
        source=source_system,
        description='Incident created via "create_fake_incident"',
    )
    for tag in Tag.objects.all()[:3]:
        IncidentTagRelation.objects.create(tag=tag, incident=incident, added_by=argus_user)
    return incident


class SourceSystemType(models.Model):
    name = models.TextField(primary_key=True, validators=[validate_lowercase])

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# Ensure that the name is always lowercase, to avoid names that only differ by case
# Note: this is not run when calling `update()` on a queryset
@receiver(pre_save, sender=SourceSystemType)
def set_name_lowercase(sender, instance: SourceSystemType, *args, **kwargs):
    instance.name = instance.name.lower()


class SourceSystem(models.Model):
    name = models.TextField()
    type = models.ForeignKey(to=SourceSystemType, on_delete=models.CASCADE, related_name="instances")
    user = models.OneToOneField(to=User, on_delete=models.CASCADE, related_name="source_system")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name", "type"], name="%(class)s_unique_name_per_type"),
        ]

    def __str__(self):
        return f"{self.name} ({self.type})"


class Tag(models.Model):
    TAG_DELIMITER = "="

    key = models.TextField(
        validators=[
            validators.RegexValidator(
                r"^[a-z0-9_]+\Z",
                message="Please enter a valid key consisting of lowercase letters, numbers and underscores.",
            )
        ]
    )
    value = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "value"], name="%(class)s_unique_key_and_value"),
        ]

    def __str__(self):
        return self.representation

    @property
    def representation(self):
        return self.join(self.key, self.value)

    @staticmethod
    def join(key, value):
        return f"{key}{Tag.TAG_DELIMITER}{value}"

    @staticmethod
    def split(tag: str):
        return tag.split(Tag.TAG_DELIMITER, maxsplit=1)


class IncidentTagRelation(models.Model):
    tag = models.ForeignKey(to=Tag, on_delete=models.CASCADE, related_name="incident_tag_relations")
    incident = models.ForeignKey(to="Incident", on_delete=models.CASCADE, related_name="incident_tag_relations")
    added_by = models.ForeignKey(to=User, on_delete=models.PROTECT, related_name="tags_added")
    added_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tag", "incident"], name="%(class)s_unique_tags_per_incident"),
        ]

    def __str__(self):
        return f"Tag <{self.tag}> on incident #{self.incident.pk} added by {self.added_by}"


class IncidentQuerySet(models.QuerySet):
    def stateful(self):
        return self.filter(end_time__isnull=False)

    def stateless(self):
        return self.filter(end_time__isnull=True)

    def active(self):
        return self.filter(end_time__gt=timezone.now())

    def inactive(self):
        return self.filter(end_time__lte=timezone.now())

    def set_active(self):
        # Don't use update(), as it doesn't trigger signals
        for incident in self.all():
            incident.set_active()

    def set_inactive(self):
        # Don't use update(), as it doesn't trigger signals
        for incident in self.all():
            incident.set_inactive()

    def prefetch_default_related(self):
        return self.prefetch_related("incident_tag_relations__tag", "source__type")


# TODO: review whether fields should be nullable, and on_delete modes
class Incident(models.Model):
    start_time = models.DateTimeField(help_text="The time the incident was created.")
    end_time = DateTimeInfinityField(
        null=True,
        blank=True,
        # TODO: add 'infinity' checkbox to admin
        help_text="The time the incident was resolved or closed. If not set, the incident is stateless;"
        " if 'infinity' is checked, the incident is stateful, but has not yet been resolved or closed - i.e. active.",
    )
    source = models.ForeignKey(
        to=SourceSystem,
        on_delete=models.CASCADE,
        related_name="incidents",
        help_text="The source system that the incident originated in.",
    )
    source_incident_id = models.TextField(verbose_name="source incident ID")
    details_url = models.TextField(blank=True, validators=[URLValidator], verbose_name="details URL")
    description = models.TextField(blank=True)
    ticket_url = models.TextField(
        blank=True,
        validators=[URLValidator],
        verbose_name="ticket URL",
        help_text="URL to existing ticket in a ticketing system.",
    )

    objects = IncidentQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_incident_id", "source"], name="%(class)s_unique_source_incident_id_per_source",
            ),
        ]
        ordering = ["-start_time"]

    def __str__(self):
        if self.end_time:
            end_time_str = f" - {get_infinity_repr(self.end_time, str_repr=True) or self.end_time}"
        else:
            end_time_str = ""
        return f"Incident #{self.pk} at {self.start_time}{end_time_str} [#{self.source_incident_id} from {self.source}]"

    def save(self, *args, **kwargs):
        # Parse and replace `end_time`, to avoid having to call `refresh_from_db()`
        self.end_time = self._meta.get_field("end_time").to_python(self.end_time)
        super().save(*args, **kwargs)

    @property
    def stateful(self):
        return self.end_time is not None

    @property
    def active(self):
        return self.stateful and self.end_time > timezone.now()

    def set_active(self):
        if not self.stateful:
            raise ValidationError("Cannot set a stateless incident as active")
        if self.active:
            return

        self.end_time = INFINITY_REPR
        self.save(update_fields=["end_time"])

    def set_inactive(self):
        if not self.stateful:
            raise ValidationError("Cannot set a stateless incident as inactive")
        if not self.active:
            return

        self.end_time = timezone.now()
        self.save(update_fields=["end_time"])

    @property
    def tags(self):
        return Tag.objects.filter(incident_tag_relations__incident=self)

    @property
    def incident_relations(self):
        return IncidentRelation.objects.filter(Q(incident1=self) | Q(incident2=self))


class IncidentRelationType(models.Model):
    name = models.TextField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class IncidentRelation(models.Model):
    # "+" prevents creating a backwards relation
    incident1 = models.ForeignKey(to=Incident, on_delete=models.CASCADE, related_name="+")
    incident2 = models.ForeignKey(to=Incident, on_delete=models.CASCADE, related_name="+")
    type = models.ForeignKey(to=IncidentRelationType, on_delete=models.CASCADE, related_name="incident_relations")
    description = models.TextField(blank=True)

    def __str__(self):
        return f"Incident #{self.incident1.pk} {self.type} #{self.incident2.pk}"