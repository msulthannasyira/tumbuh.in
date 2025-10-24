"""Quick manual check for Gemini Grounding response.

Run with:
    python scripts/test_gemini_grounding.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise SystemExit("GOOGLE_API_KEY belum diset di .env")

client = genai.Client(api_key=GOOGLE_API_KEY)
model_name = "gemini-2.5-flash"
config = types.GenerateContentConfig(
    tools=[types.Tool(google_search=types.GoogleSearch())],
    temperature=0.3,
)

prompt = (
    "Anda adalah agronom ahli. Berikan contoh singkat top 3 tanaman potensial untuk lahan "
    "sawah dataran rendah dengan curah hujan 2000 mm/tahun di Jawa Barat. "
    "Jawab menggunakan JSON dengan struktur {\"plants\":[{\"name\":str,\"confidence\":0-1,\"rationale\":str}]}."
)

response = client.models.generate_content(
    model=model_name,
    contents=[
        types.Content(
            role="user",
            parts=[types.Part.from_text(prompt)],
        )
    ],
    config=config,
)

def _collect_text(resp: types.GenerateContentResponse) -> str:
    if resp.text:
        return resp.text
    collected = []
    for candidate in getattr(resp, "candidates", []) or []:
        for part in getattr(candidate, "content", types.Content(parts=[])).parts:
            if getattr(part, "text", None):
                collected.append(part.text)
    return "".join(collected)


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


raw = _strip_markdown(_collect_text(response))

try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:  # pragma: no cover - manual diagnostics
    print("Gagal parse JSON:", exc)
    print("Raw response:\n", raw)
    raise SystemExit(1) from exc

print(json.dumps(payload, indent=2, ensure_ascii=False))
