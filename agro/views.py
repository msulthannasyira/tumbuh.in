from __future__ import annotations

import json
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, List, Tuple
import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.db import transaction
from django.db.models import Avg, Count
from django.db.models.functions import TruncDate
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from django.urls import reverse_lazy

from agro.models import Area, Tile
from agro.services.aggregator import build_summary
from agro.services.gemini_service import GeminiCropAdvisor
from agro.services.gee_service import GEEVariableService
from agro.services.serializers import area_to_dict, tile_to_dict
from agro.services.hyperlocal import build_hyperlocal_payload
from agro.services.tiling import PolygonTiler

try:  # Optional dependency guard for numpy values from APIs
	import numpy as np
except ImportError:  # pragma: no cover - numpy is in requirements but guard just in case
	np = None

# Module logger so messages appear in the terminal when Django is run
logger = logging.getLogger(__name__)
if not logger.handlers:
	# Ensure there is at least a console handler during development so INFO logs are visible
	logging.basicConfig(level=logging.INFO)


class TumbuhLoginView(LoginView):
	template_name = "auth/login.html"
	redirect_authenticated_user = True
	success_url = reverse_lazy("agro:dashboard")

	def get_form(self, form_class=None):
		form = super().get_form(form_class)
		_prepare_auth_form(
			form,
			{
				"username": "Username",
				"password": "Password",
			},
			password_autocomplete="current-password",
		)
		return form


class TumbuhLogoutView(LogoutView):
	next_page = reverse_lazy("agro:landing")


def _prepare_auth_form(form, placeholders=None, password_autocomplete="new-password"):
	placeholders = placeholders or {}
	for name, field in form.fields.items():
		existing_classes = field.widget.attrs.get("class", "")
		field.widget.attrs["class"] = (existing_classes + " auth-input").strip()
		if name in placeholders:
			field.widget.attrs.setdefault("placeholder", placeholders[name])
		if field.widget.input_type == "password":
			field.widget.attrs.setdefault("autocomplete", password_autocomplete)
		else:
			field.widget.attrs.setdefault("autocomplete", "off")
		field.help_text = ""


def register(request: HttpRequest) -> HttpResponse:
	if request.user.is_authenticated:
		return redirect("agro:dashboard")

	if request.method == "POST":
		form = UserCreationForm(request.POST)
		if form.is_valid():
			user = form.save()
			login(request, user)
			return redirect("agro:dashboard")
	else:
		form = UserCreationForm()

	_prepare_auth_form(
		form,
		{
			"username": "Username",
			"password1": "Password",
			"password2": "Ulangi Password",
		},
	)

	return render(request, "auth/register.html", {"form": form})


def landing(request: HttpRequest) -> HttpResponse:
	area_stats = Area.objects.aggregate(total_areas=Count("id"), avg_processing=Avg("processing_seconds"))
	total_tiles = Tile.objects.count()
	enriched_tiles = Tile.objects.filter(status=Tile.Status.ENRICHED).count()
	active_areas = Area.objects.exclude(status=Area.Status.FAILED).count()
	latest_area = Area.objects.order_by("-created_at").first()

	today = timezone.now().date()
	areas_today = Area.objects.filter(created_at__date=today).count()
	tile_density = Tile.objects.values("area_id").annotate(total=Count("id")).aggregate(avg=Avg("total"))
	avg_tiles_per_area = tile_density.get("avg") or 0.0

	recent_qs = Area.objects.annotate(tile_total=Count("tiles")).order_by("-created_at")
	paginator = Paginator(recent_qs, 5)
	page_number = request.GET.get("page")
	recent_page = paginator.get_page(page_number)

	status_qs = Area.objects.values("status").annotate(total=Count("id"))
	status_labels = dict(Area.Status.choices)
	status_total = sum(entry["total"] for entry in status_qs) or 1
	status_summary = [
		{
			"status": entry["status"],
			"label": status_labels.get(entry["status"], entry["status"]),
			"total": entry["total"],
			"percent": (entry["total"] / status_total) * 100,
		}
		for entry in status_qs
	]
	status_summary.sort(key=lambda item: item["total"], reverse=True)

	top_counter: Counter = Counter()
	for tile in Tile.objects.exclude(gemini_recommendations=[]).only("gemini_recommendations").iterator():
		recommendations = tile.gemini_recommendations or []
		if recommendations:
			plant = recommendations[0].get("plant") or "Tidak diketahui"
			top_counter[plant] += 1

	total_top = sum(top_counter.values()) or 1
	top_crops = [
		{
			"plant": plant,
			"count": count,
			"share": (count / total_top) * 100,
		}
		for plant, count in top_counter.most_common(5)
	]

	lookback = today - timedelta(days=6)
	trend_raw = (
		Area.objects.filter(created_at__date__gte=lookback)
		.annotate(day=TruncDate("created_at"))
		.values("day")
		.annotate(total=Count("id"))
		.order_by("day")
	)
	trend_map = {item["day"]: item["total"] for item in trend_raw}
	processing_trend = []
	for offset in range(7):
		day = lookback + timedelta(days=offset)
		total = trend_map.get(day, 0)
		processing_trend.append({"day": day, "total": total})
	trend_scale = max((point["total"] for point in processing_trend), default=0) or 1
	for point in processing_trend:
		point["percent"] = (point["total"] / trend_scale) * 100 if trend_scale else 0

	context = {
		"stats": {
			"total_areas": area_stats.get("total_areas", 0) or 0,
			"total_tiles": total_tiles,
			"enriched_tiles": enriched_tiles,
			"active_areas": active_areas,
			"areas_today": areas_today,
			"avg_processing": area_stats.get("avg_processing") or 0.0,
			"avg_tiles_per_area": avg_tiles_per_area,
		},
		"latest_area": latest_area,
		"recent_page": recent_page,
		"status_summary": status_summary,
		"top_crops": top_crops,
		"processing_trend": processing_trend,
		"trend_scale": trend_scale,
	}

	return render(request, "agro/landing.html", context)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
	return render(request, "agro/home.html")


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def process_area(request: HttpRequest) -> JsonResponse:
	try:
		payload = json.loads(request.body.decode("utf-8"))
	except json.JSONDecodeError:
		return JsonResponse({"status": "error", "message": "Payload tidak valid"}, status=400)

	geometry = payload.get("geometry")
	if not geometry:
		return JsonResponse({"status": "error", "message": "GeoJSON polygon wajib diisi."}, status=400)

	name = payload.get("name")
	tile_size_raw = payload.get("tile_size", 15)
	try:
		tile_size = int(tile_size_raw)
	except (TypeError, ValueError):
		return JsonResponse({"status": "error", "message": "Ukuran tile harus berupa angka."}, status=400)

	if tile_size < 5 or tile_size > 100:
		return JsonResponse({"status": "error", "message": "Ukuran tile harus antara 5 dan 100 meter."}, status=400)
	tiler = PolygonTiler(tile_size)

	try:
		matrix, tile_geoms = tiler.tile(geometry)
	except ValueError as exc:
		return JsonResponse({"status": "error", "message": str(exc)}, status=400)

	start_timer = time.perf_counter()

	with transaction.atomic():
		area = Area.objects.create(
			name=name,
			geometry=geometry,
			matrix=matrix,
			tile_size_m=tile_size,
			status=Area.Status.COLLECTING,
		)

		tiles = _collect_variables(area, tile_geoms)
		area.status = Area.Status.COLLECTED

		try:
			gemini = GeminiCropAdvisor(api_key=settings.GOOGLE_API_KEY)
			recommendations = gemini.recommend(
				{
					"area_id": str(area.id),
					"tiles": [
						{
							"row": tile.row_index,
							"col": tile.col_index,
							"centroid": {"lat": tile.centroid_lat, "lon": tile.centroid_lon},
							"variables": tile.variables,
						}
						for tile in tiles
					],
				}
			)
			_apply_recommendations(tiles, recommendations)
			area.status = Area.Status.ENRICHED
		except Exception as exc:  # pragma: no cover
			area.status = Area.Status.FAILED
			area.metadata = {"gemini_error": str(exc)}

		area.processing_seconds = time.perf_counter() - start_timer
		area.save()

	aggregates = build_summary(area, tiles)
	response_tiles = [tile_to_dict(tile) for tile in tiles]

	return JsonResponse(
		{
			"status": "success",
			"area": area_to_dict(area),
			"tiles": response_tiles,
			"aggregates": aggregates,
		},
		status=200,
	)


@login_required
@require_GET
def get_area_detail(request: HttpRequest, area_id) -> JsonResponse:
	area = get_object_or_404(Area, id=area_id)
	tiles = list(area.tiles.all())
	aggregates = build_summary(area, tiles)
	return JsonResponse(
		{
			"status": "success",
			"area": area_to_dict(area),
			"aggregates": aggregates,
		}
	)


@login_required
@require_GET
def get_area_tiles(request: HttpRequest, area_id) -> JsonResponse:
	area = get_object_or_404(Area, id=area_id)
	tiles = [tile_to_dict(tile) for tile in area.tiles.all()]
	return JsonResponse({"status": "success", "tiles": tiles})


@login_required
def area_insight(request: HttpRequest, area_id) -> HttpResponse:
	area = get_object_or_404(Area, id=area_id)
	tiles = list(area.tiles.order_by("row_index", "col_index"))
	aggregates = build_summary(area, tiles)
	tile_count = aggregates.get("tile_count") or len(tiles)
	env_summary = aggregates.get("env_summary") or {}

	palette = [
		"#88c34e",
		"#73cf7b",
		"#96d990",
		"#5b7b45",
		"#bedf9d",
		"#d7f1c0",
		"#4b6d33",
	]
	color_map: dict[str, str] = {}

	def get_color(label: str | None) -> str:
		key = label or "Tidak diketahui"
		if key not in color_map:
			color_map[key] = palette[len(color_map) % len(palette)]
		return color_map[key]

	def _split_value_unit(value: Any) -> Tuple[str | None, str | None]:
		if value is None:
			return None, None
		if isinstance(value, (int, float)):
			return f"{value}", ""
		text = str(value).strip()
		if not text:
			return None, None
		parts = text.replace(",", ".").split()
		primary = parts[0]
		unit = " ".join(parts[1:]) if len(parts) > 1 else ""
		return primary, unit

	top_crops = []
	for item in (aggregates.get("dominant_crops") or [])[:5]:
		plant = item.get("plant") or "Tidak diketahui"
		share = (item.get("tiles", 0) / tile_count * 100) if tile_count else 0
		color = get_color(plant)
		top_crops.append(
			{
				"plant": plant,
				"tiles": item.get("tiles", 0),
				"avg_confidence": (item.get("avg_confidence", 0) or 0) * 100,
				"share": share,
				"color": color,
			}
		)

	top_primary = top_crops[0] if top_crops else None

	primary_crops: list[str] = []
	seen_primary: set[str] = set()
	for crop in top_crops:
		plant_name = crop.get("plant")
		if not plant_name or plant_name in seen_primary or plant_name == "Tidak diketahui":
			continue
		primary_crops.append(plant_name)
		seen_primary.add(plant_name)
	if not primary_crops and top_crops:
		fallback = top_crops[0].get("plant")
		if fallback and fallback != "Tidak diketahui":
			primary_crops.append(fallback)

	hyperlocal_context = {
		"area_id": str(area.id),
		"area_name": area.name or "Tanpa Nama",
		"primary_crops": primary_crops,
	}

	metadata = dict(area.metadata or {})
	stored_context = metadata.get("hyperlocal_context")
	hyperlocal_stored = metadata.get("hyperlocal")
	if not hyperlocal_stored or stored_context != hyperlocal_context:
		hyperlocal_stored = build_hyperlocal_payload(
			area.geometry,
			area.matrix,
			tiles,
			context=hyperlocal_context,
		)
		if hyperlocal_stored:
			metadata["hyperlocal"] = hyperlocal_stored
			metadata["hyperlocal_context"] = hyperlocal_context
			area.metadata = metadata
			area.save(update_fields=["metadata", "updated_at"])

	hyperlocal_items = (hyperlocal_stored or {}).get("items", [])
	hyperlocal_generated = (hyperlocal_stored or {}).get("generated_at")
	hyperlocal_display = None
	if hyperlocal_generated:
		try:
			parsed = datetime.fromisoformat(hyperlocal_generated)
			if parsed.tzinfo is None:
				parsed = parsed.replace(tzinfo=timezone.utc)
			hyperlocal_display = timezone.localtime(parsed)
		except ValueError:
			hyperlocal_display = None

	tile_size = area.tile_size_m or 15
	approx_area = tile_count * (tile_size ** 2) / 10000 if tile_count else 0
	dashboard_metrics = []
	dashboard_metrics.append(
		{
			"label": "Grid Terpetakan",
			"value": f"{tile_count}",
			"unit": "tile",
			"subtitle": f"Grid {tile_size} m",
		}
	)
	if approx_area:
		dashboard_metrics.append(
			{
				"label": "Perkiraan Luas",
				"value": f"{approx_area:.2f}",
				"unit": "ha",
				"subtitle": "Dihitung dari grid",
			}
		)
	if top_primary:
		dashboard_metrics.append(
			{
				"label": "Dominasi Tanaman",
				"value": f"{top_primary['share']:.1f}",
				"unit": "%",
				"subtitle": top_primary.get("plant", "-"),
			}
		)
		confidence = top_primary.get("avg_confidence")
		if confidence is not None:
			dashboard_metrics.append(
				{
					"label": "Kepercayaan AI",
					"value": f"{confidence:.0f}",
					"unit": "%",
					"subtitle": "Rata-rata rekomendasi",
				}
			)

	ndvi_value, ndvi_unit = _split_value_unit(env_summary.get("Rata-rata NDVI"))
	if ndvi_value:
		dashboard_metrics.append(
			{
				"label": "Rata-rata NDVI",
				"value": ndvi_value,
				"unit": ndvi_unit or "",
				"subtitle": "Kepadatan vegetasi",
			}
		)
	ndwi_value, ndwi_unit = _split_value_unit(env_summary.get("Rata-rata NDWI"))
	if ndwi_value:
		dashboard_metrics.append(
			{
				"label": "Rata-rata NDWI",
				"value": ndwi_value,
				"unit": ndwi_unit or "",
				"subtitle": "Kelembapan lahan",
			}
		)
	precip_value, precip_unit = _split_value_unit(env_summary.get("Curah Hujan Bulanan"))
	if precip_value:
		dashboard_metrics.append(
			{
				"label": "Curah Hujan",
				"value": precip_value,
				"unit": precip_unit or "mm",
				"subtitle": "Rata-rata bulanan",
			}
		)

	env_icon_map = {
		"Rata-rata NDVI": "ND",
		"Rata-rata NDWI": "NW",
		"Curah Hujan Bulanan": "CH",
	}
	env_items = []
	for label, value in env_summary.items():
		initial = "".join(word[0].upper() for word in label.split() if word)[:2] or "EN"
		env_items.append(
			{
				"label": label,
				"value": value,
				"badge": env_icon_map.get(label, initial),
			}
		)

	tile_rows = [_serialize_tile(tile) for tile in tiles]
	tile_features = []
	for tile, row in zip(tiles, tile_rows):
		top_rec = row["recommendations"][0] if row["recommendations"] else None
		plant_name = top_rec.get("plant") if top_rec else None
		color = get_color(plant_name)
		tile_features.append(
			{
				"geometry": tile.geometry,
				"centroid": {"lat": tile.centroid_lat, "lon": tile.centroid_lon},
				"top": top_rec,
				"index": row["index"],
				"color": color,
			}
		)
	paginator = Paginator(tile_rows, 5)
	page_number = request.GET.get("page")
	tile_page = paginator.get_page(page_number)

	legend_items = []
	seen = set()
	for crop in top_crops:
		legend_items.append({"plant": crop["plant"], "color": crop["color"]})
		seen.add(crop["plant"])
	for plant, color in color_map.items():
		if plant not in seen:
			legend_items.append({"plant": plant, "color": color})
			seen.add(plant)
	legend_items = legend_items[:10]

	context = {
		"area": area,
		"status_label": area.get_status_display(),
		"tile_count": tile_count,
		"processing_seconds": aggregates.get("processing_seconds", 0),
		"dashboard_metrics": dashboard_metrics,
		"top_crops": top_crops,
		"top_primary": top_primary,
		"env_summary": env_summary,
		"env_items": env_items,
		"tile_page": tile_page,
		"tile_features": tile_features,
		"legend_items": legend_items,
		"hyperlocal_items": hyperlocal_items,
		"hyperlocal_generated": hyperlocal_generated,
		"hyperlocal_generated_display": hyperlocal_display,
		"hyperlocal_context": hyperlocal_context,
	}
	return render(request, "agro/area_detail.html", context)


@login_required
@require_GET
def refresh_hyperlocal(request: HttpRequest, area_id) -> JsonResponse:
	area = get_object_or_404(Area, id=area_id)
	tiles_qs = list(area.tiles.order_by("row_index", "col_index"))
	tile_payload = [
		{
			"centroid_lat": tile.centroid_lat,
			"centroid_lon": tile.centroid_lon,
			"geometry": tile.geometry,
		}
		for tile in tiles_qs
	]
	aggregates = build_summary(area, tiles_qs)
	primary_crops: list[str] = []
	seen_primary: set[str] = set()
	for item in (aggregates.get("dominant_crops") or [])[:5]:
		plant = item.get("plant")
		if not plant or plant in seen_primary or plant == "Tidak diketahui":
			continue
		primary_crops.append(plant)
		seen_primary.add(plant)
	if not primary_crops and aggregates.get("dominant_crops"):
		fallback = aggregates["dominant_crops"][0].get("plant")
		if fallback and fallback != "Tidak diketahui":
			primary_crops.append(fallback)

	hyperlocal_context = {
		"area_id": str(area.id),
		"area_name": area.name or "Tanpa Nama",
		"primary_crops": primary_crops,
	}

	payload = build_hyperlocal_payload(
		area.geometry,
		area.matrix,
		tile_payload,
		context=hyperlocal_context,
	)
	if not payload:
		return JsonResponse(
			{"status": "error", "message": "Tidak dapat menghasilkan insight hyperlokal untuk area ini."},
			status=400,
		)

	metadata = dict(area.metadata or {})
	metadata["hyperlocal"] = payload
	metadata["hyperlocal_context"] = hyperlocal_context
	area.metadata = metadata
	area.save(update_fields=["metadata", "updated_at"])

	generated_display = None
	generated_at = payload.get("generated_at")
	if generated_at:
		try:
			parsed = datetime.fromisoformat(generated_at)
			if parsed.tzinfo is None:
				parsed = parsed.replace(tzinfo=timezone.utc)
			generated_display = timezone.localtime(parsed).strftime("%d %b %Y %H:%M")
		except ValueError:
			generated_display = None

	return JsonResponse(
		{
			"status": "success",
			"generated_at": generated_at,
			"generated_at_display": generated_display,
			"items": payload.get("items", []),
			"context": hyperlocal_context,
		}
	)


def _collect_variables(area: Area, tile_geoms) -> List[Tile]:
	today = datetime.utcnow().date()
	start = today - timedelta(days=7)
	tiles: List[Tile] = []

	valid_tiles = [tile_geom for tile_geom in tile_geoms if tile_geom.centroid[0] is not None and tile_geom.centroid[1] is not None]
	if not valid_tiles:
		return tiles
	max_workers = getattr(settings, "GEE_MAX_WORKERS", 4)
	max_workers = max(1, min(max_workers, len(valid_tiles)))

	results = []
	# Keep track of futures so we can log progress and duration per tile
	with ThreadPoolExecutor(max_workers=max_workers) as executor:
		futures = {}
		starts: dict = {}

		logger.info("Memulai pengumpulan variabel GEE untuk %d tile menggunakan %d worker(s)", len(valid_tiles), max_workers)

		for tile_geom in valid_tiles:
			lat, lon = tile_geom.centroid
			future = executor.submit(
				GEEVariableService.collect,
				lat=lat,
				lon=lon,
				start_date=str(start),
				end_date=str(today),
			)
			futures[future] = tile_geom
			starts[future] = time.perf_counter()
			logger.debug("Submitted tile %d,%d (centroid=%.6f,%.6f)", tile_geom.row, tile_geom.col, lat, lon)

		for future in as_completed(futures):
			tile_geom = futures[future]
			start_time = starts.pop(future, None)
			try:
				raw_variables = future.result()
				duration = (time.perf_counter() - start_time) if start_time is not None else None
				logger.info(
					"Tile %d,%d collected%s",
					tile_geom.row,
					tile_geom.col,
					f" in {duration:.2f}s" if duration is not None else "",
				)
			except Exception as exc:  # pragma: no cover - defensive
				duration = (time.perf_counter() - start_time) if start_time is not None else None
				logger.error(
					"Tile %d,%d failed: %s%s",
					tile_geom.row,
					tile_geom.col,
					str(exc),
					f" (after {duration:.2f}s)" if duration is not None else "",
				)
				raw_variables = {"status": "error", "message": str(exc)}
			results.append((tile_geom, _make_json_safe(raw_variables)))

	for tile_geom, variables in sorted(results, key=lambda item: (item[0].row, item[0].col)):
		lat, lon = tile_geom.centroid
		tile = Tile.objects.create(
			area=area,
			row_index=tile_geom.row,
			col_index=tile_geom.col,
			centroid_lat=lat,
			centroid_lon=lon,
			geometry=tile_geom.to_geojson(),
			variables=variables,
			status=Tile.Status.COLLECTED,
		)
		logger.debug("Tile db created %s (%d,%d)", tile.id, tile.row_index, tile.col_index)
		tiles.append(tile)
	return tiles


def _serialize_tile(tile: Tile) -> dict:
	vars = tile.variables or {}
	seasonal = vars.get("seasonal", {})
	climate = vars.get("climate", {})
	climate_data = climate.get("data")
	soil = vars.get("soil", {})
	soil_props = soil.get("properties_at_0_5cm", {})
	topography = vars.get("topography", {})
	landcover = vars.get("landcover", {}).get("vegetation_indices", {})

	precip = seasonal.get("data", {}).get("long_term_avg_precip_mm")
	if precip is None:
		precip = seasonal.get("long_term_avg_precip_mm")

	if isinstance(climate_data, list) and climate_data:
		temperature = climate_data[0].get("temp_mean_c")
	else:
		temperature = climate.get("temp_mean_c")

	ph = soil_props.get("ph", soil.get("ph"))
	sand = soil_props.get("sand_g_kg", soil.get("sand_g_kg"))
	clay = soil_props.get("clay_g_kg", soil.get("clay_g_kg"))
	elevation = topography.get("data", {}).get("elevation_meters", topography.get("elevation_meters"))
	ndvi = landcover.get("ndvi")
	ndwi = landcover.get("ndwi")

	recs = tile.gemini_recommendations or []
	rec_list = []
	for idx, rec in enumerate(recs, start=1):
		confidence = rec.get("confidence")
		rec_list.append(
			{
				"rank": idx,
				"plant": rec.get("plant") or "Tidak diketahui",
				"confidence": confidence * 100 if isinstance(confidence, (int, float)) else None,
				"rationale": rec.get("rationale"),
			}
		)

	return {
		"index": f"{tile.row_index},{tile.col_index}",
		"lat": _format_metric(tile.centroid_lat, 5),
		"lon": _format_metric(tile.centroid_lon, 5),
		"precip": _format_metric(precip, 1, " mm"),
		"temperature": _format_metric(temperature, 1, " °C"),
		"ph": _format_metric(ph, 2),
		"texture": _format_texture(sand, clay),
		"elevation": _format_metric(elevation, 0, " m"),
		"ndvi": _format_metric(ndvi, 3),
		"ndwi": _format_metric(ndwi, 3),
		"recommendations": rec_list[:5],
	}


def _format_metric(value, digits=None, suffix="") -> str:
	try:
		numeric = float(value)
	except (TypeError, ValueError):
		return "—"
	if math.isnan(numeric) or math.isinf(numeric):
		return "—"
	if digits is not None:
		formatted = f"{numeric:.{digits}f}"
	else:
		formatted = f"{numeric}"
	return f"{formatted}{suffix}"


def _format_texture(sand, clay) -> str:
	parts = []
	for label, value in (("Sand", sand), ("Clay", clay)):
		try:
			numeric = float(value)
		except (TypeError, ValueError):
			continue
		if math.isnan(numeric) or math.isinf(numeric):
			continue
		parts.append(f"{label} {numeric:.0f} g/kg")
	return " · ".join(parts) if parts else "—"


def _apply_recommendations(tiles: List[Tile], recommendations: dict) -> None:
	mapping = {}
	if "tiles" in recommendations:
		for tile in recommendations["tiles"]:
			mapping[tile.get("tile_id")] = tile.get("recommendations", [])
	for tile in tiles:
		key = f"{tile.row_index}-{tile.col_index}"
		tile.gemini_recommendations = mapping.get(key, [])
		if tile.gemini_recommendations:
			tile.status = Tile.Status.ENRICHED
		tile.save(update_fields=["gemini_recommendations", "status", "updated_at"])


def _make_json_safe(value: Any) -> Any:
	if isinstance(value, dict):
		return {str(k): _make_json_safe(v) for k, v in value.items()}
	if isinstance(value, list):
		return [_make_json_safe(item) for item in value]
	if isinstance(value, tuple):
		return [_make_json_safe(item) for item in value]
	if isinstance(value, set):
		return [_make_json_safe(item) for item in value]

	if isinstance(value, (datetime, date)):
		return value.isoformat()
	if hasattr(value, "isoformat") and callable(value.isoformat):
		try:
			return value.isoformat()
		except Exception:  # pragma: no cover
			pass

	if isinstance(value, Decimal):
		return float(value)

	if isinstance(value, float):
		if math.isnan(value) or math.isinf(value):
			return None
		return value

	if np is not None:
		if isinstance(value, (np.integer,)):
			return int(value)
		if isinstance(value, (np.floating,)):
			float_value = float(value)
			if math.isnan(float_value) or math.isinf(float_value):
				return None
			return float_value
		if isinstance(value, np.ndarray):
			return [_make_json_safe(item) for item in value.tolist()]

	if isinstance(value, (int, str, bool)) or value is None:
		return value

	return str(value)
