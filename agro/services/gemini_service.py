from __future__ import annotations

import json
import os
from typing import Dict, List

from google import genai
from google.genai import types

GROUNDING_TOOL = types.Tool(google_search=types.GoogleSearch())


class GeminiCropAdvisor:
    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash") -> None:
        key = api_key or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise EnvironmentError("GOOGLE_API_KEY belum diset.")
        self.client = genai.Client(api_key=key)
        self.model = model
        self.config = types.GenerateContentConfig(
            tools=[GROUNDING_TOOL],
            temperature=0.4,
        )

    def recommend(self, area_payload: Dict) -> Dict[str, List[Dict]]:
        contents = self._build_prompt(area_payload)
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=self.config,
        )
        raw_text = _strip_markdown(_extract_text(response))
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Respons Gemini tidak berbentuk JSON yang valid."
                f" Detail: {exc}\nRaw: {raw_text}"
            )
        return data

    def _build_prompt(self, payload: Dict) -> List[types.Content]:
        instruction = (
            "Anda adalah agronom ahli. Analisis variabel lingkungan setiap tile dan sarankan 5 tanaman yang paling potensial. "
            "Berikan output JSON dengan struktur: { \"tiles\": [ { \"tile_id\": \"row-col\", \"recommendations\": [ {\"plant\": str, \"confidence\": float 0-1, \"rationale\": str } ] } ] }. "
            "Wajib mengembalikan confidence numerik antara 0 dan 1. Gunakan grounding untuk validasi ilmiah."
        )
        tiles = payload.get("tiles", [])
        prompt_tiles = []
        for tile in tiles:
            prompt_tiles.append(
                {
                    "tile_id": f"{tile['row']}-{tile['col']}",
                    "coordinates": tile.get("centroid"),
                    "variables": tile.get("variables"),
                }
            )
        return [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(instruction),
                    types.Part.from_text(json.dumps({"tiles": prompt_tiles}, ensure_ascii=False)),
                ],
            )
        ]


def _extract_text(response: types.GenerateContentResponse) -> str:
    if getattr(response, "text", None):
        return response.text

    chunks: List[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        for part in getattr(candidate, "content", types.Content(parts=[])).parts:
            if getattr(part, "text", None):
                chunks.append(part.text)
    if not chunks:
        raise ValueError("Gemini tidak mengembalikan teks apa pun.")
    return "".join(chunks)


def _strip_markdown(text: str) -> str:
    cleaned = text.strip()
    fence = "```"
    if fence in cleaned:
        start = cleaned.find(fence)
        after = cleaned[start + len(fence) :]
        if "\n" in after:
            _, after = after.split("\n", 1)
        end = after.find(fence)
        content = after[:end] if end != -1 else after
        return content.strip()
    return cleaned
