import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from agro.models import Area, Tile
from agro.services.tiling import PolygonTiler


class TilingServiceTests(TestCase):
	def test_polygon_tiler_creates_tiles(self):
		geojson = {
			"type": "Polygon",
			"coordinates": [
				[
					[107.0, -6.53],
					[107.001, -6.53],
					[107.001, -6.529],
					[107.0, -6.529],
					[107.0, -6.53],
				]
			],
		}

		tiler = PolygonTiler(tile_size_m=30)
		matrix, tiles = tiler.tile(geojson)

		self.assertTrue(matrix)
		self.assertTrue(tiles)
		for tile in tiles:
			self.assertIsNotNone(tile.centroid[0])
			self.assertIsNotNone(tile.centroid[1])
			self.assertEqual(tile.to_geojson()["type"], "Polygon")


class ProcessAreaViewTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.payload = {
			"name": "Lahan Uji",
			"tile_size": 25,
			"geometry": {
				"type": "Polygon",
				"coordinates": [
					[
						[107.0, -6.53],
						[107.0005, -6.53],
						[107.0005, -6.5295],
						[107.0, -6.5295],
						[107.0, -6.53],
					]
				],
			},
		}

	@patch("agro.views.GEEVariableService.collect")
	@patch("agro.views.GeminiCropAdvisor")
	def test_process_area_success(self, mock_gemini_cls, mock_collect):
		mock_collect.return_value = {
			"climate": {"status": "success", "data": []},
			"soil": {"status": "success", "properties_at_0_5cm": {}},
			"topography": {"status": "success", "data": {}},
			"landcover": {"status": "success", "land_cover": {}, "vegetation_indices": {}},
			"seasonal": {"status": "success", "data": {"long_term_avg_precip_mm": 100}},
			"nighttime": {"status": "success", "data": {}},
		}

		mock_gemini = mock_gemini_cls.return_value
		mock_gemini.recommend.return_value = {
			"tiles": [
				{
					"tile_id": "0-0",
					"recommendations": [
						{"plant": "Padi", "confidence": 0.92, "rationale": "Cocok"}
					],
				}
			]
		}

		response = self.client.post(
			reverse("agro:process-area"),
			data=json.dumps(self.payload),
			content_type="application/json",
		)
		self.assertEqual(response.status_code, 200)
		data = response.json()
		self.assertEqual(data["status"], "success")
		self.assertEqual(Area.objects.count(), 1)
		self.assertEqual(Area.objects.first().tile_size_m, 25)
		self.assertGreaterEqual(Tile.objects.count(), 1)
		self.assertTrue(data["tiles"][0]["recommendations"])

	@patch("agro.views.GEEVariableService.collect")
	@patch("agro.views.GeminiCropAdvisor")
	def test_process_area_invalid_tile_size(self, mock_gemini_cls, mock_collect):
		self.payload["tile_size"] = 3
		response = self.client.post(
			reverse("agro:process-area"),
			data=json.dumps(self.payload),
			content_type="application/json",
		)
		self.assertEqual(response.status_code, 400)
		self.assertEqual(Area.objects.count(), 0)
