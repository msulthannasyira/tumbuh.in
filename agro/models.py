import uuid

from django.db import models


class Area(models.Model):
	class Status(models.TextChoices):
		COLLECTING = "collecting", "Collecting Variables"
		COLLECTED = "collected", "Collected"
		ENRICHING = "enriching", "Enriching with Gemini"
		ENRICHED = "enriched", "Completed"
		FAILED = "failed", "Failed"

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	name = models.CharField(max_length=140, blank=True, null=True)
	geometry = models.JSONField(help_text="Original GeoJSON polygon submitted by user")
	matrix = models.JSONField(help_text="2D matrix of tile centroid coordinates", default=list)
	tile_size_m = models.PositiveIntegerField(default=15)
	metadata = models.JSONField(blank=True, null=True, default=dict)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.COLLECTING)
	processing_seconds = models.FloatField(blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-created_at"]

	def __str__(self) -> str:  # pragma: no cover - simple representation
		return f"Area {self.id}" if not self.name else self.name


class Tile(models.Model):
	class Status(models.TextChoices):
		PENDING = "pending", "Pending"
		COLLECTED = "collected", "Variables Collected"
		ENRICHED = "enriched", "Enriched"
		FAILED = "failed", "Failed"

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	area = models.ForeignKey(Area, related_name="tiles", on_delete=models.CASCADE)
	row_index = models.PositiveIntegerField()
	col_index = models.PositiveIntegerField()
	centroid_lat = models.FloatField()
	centroid_lon = models.FloatField()
	geometry = models.JSONField(help_text="GeoJSON polygon of tile bounds")
	variables = models.JSONField(default=dict)
	gemini_recommendations = models.JSONField(default=list)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("area", "row_index", "col_index")
		ordering = ["row_index", "col_index"]

	def __str__(self) -> str:  # pragma: no cover
		return f"Tile {self.row_index},{self.col_index}"
