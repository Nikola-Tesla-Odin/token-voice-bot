"""Images for posts: free quote cards (default) or Gemini illustrations, hosted on Cloudinary for Buffer.

Settings (in ~/.persona-agent/.env):
  PERSONA_AGENT_IMAGES       card (default, free) | gemini (paid API) | off
  GEMINI_API_KEY             from Google AI Studio (aistudio.google.com/apikey)
  GEMINI_IMAGE_MODEL         default gemini-nano-banana-2.1 (~$0.034 per 1K image)
  GEMINI_BUDGET_USD          monthly cap for images, default 8 (separate from the Claude budget)
  PERSONA_AGENT_IMAGE_EVERY  attach an image to every Nth post (default: 1 for cards, 2 for gemini)
  CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET   from the Cloudinary dashboard
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date

from .persona import HOME, Persona

MODE = os.environ.get("PERSONA_AGENT_IMAGES", "card")
MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1")
BUDGET_USD = float(os.environ.get("GEMINI_BUDGET_USD", "8"))
IMAGE_EVERY = int(os.environ.get("PERSONA_AGENT_IMAGE_EVERY", "1" if MODE == "card" else "2"))
# Standard-tier price of one 1K image (ai.google.dev/gemini-api/docs/pricing); unknown models priced high.
PRICE_PER_IMAGE = {
    "gemini-nano-banana-2.1": 0.0336,
    "gemini-3.1-flash-lite-image": 0.0336,
    "gemini-3.1-flash-image": 0.067,
    "gemini-3-pro-image": 0.134,
}.get(MODEL, 0.15)


@dataclass
class Media:
    data: bytes
    mime_type: str
    url: str = ""  # public URL once hosted


def enabled() -> bool:
    return MODE == "card" or (MODE == "gemini" and bool(os.environ.get("GEMINI_API_KEY")))


def hosting_configured() -> bool:
    return all(os.environ.get(k) for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))


# --- budget -----------------------------------------------------------------------------------

def _ledger_path():
    return HOME / "spend" / f"{date.today():%Y-%m}-images.json"


def spent() -> float:
    path = _ledger_path()
    return json.loads(path.read_text())["usd"] if path.exists() else 0.0


def _record() -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {"usd": 0.0, "images": 0}
    data["usd"] += PRICE_PER_IMAGE
    data["images"] += 1
    path.write_text(json.dumps(data))


def can_afford() -> bool:
    return spent() + PRICE_PER_IMAGE <= BUDGET_USD


# --- generation -------------------------------------------------------------------------------

def prompt_for(persona: Persona, scene: str) -> str:
    return (
        f"{scene}\n\nStyle: a painted editorial illustration in the world of {persona.name}, "
        "clearly an artwork (not a photograph), warm and detailed, with modern elements blended in. "
        "No text, letters, logos, or watermarks in the image."
    )


def _find_image(node) -> tuple[str, str] | None:
    """Return (base64 data, mime type) of the first image block anywhere in the response."""
    if isinstance(node, dict):
        if node.get("type") == "image" and node.get("data"):
            return node["data"], node.get("mime_type", "image/png")
        inline = node.get("inline_data") or node.get("inlineData")
        if isinstance(inline, dict) and inline.get("data"):
            return inline["data"], inline.get("mime_type") or inline.get("mimeType", "image/png")
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        if found := _find_image(child):
            return found
    return None


def generate(persona: Persona, scene: str) -> Media:
    body = {
        "model": MODEL,
        "input": [{"type": "text", "text": prompt_for(persona, scene)}],
        "response_format": {"type": "image", "aspect_ratio": "16:9", "image_size": "1K"},
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini image request failed ({e.code}): {e.read()[:300].decode(errors='replace')}")
    _record()  # billed once the model has run, whether or not an image came back
    found = _find_image(result)
    if not found:
        raise RuntimeError(f"Gemini returned no image: {json.dumps(result)[:300]}")
    data, mime = found
    return Media(base64.b64decode(data), mime)


# --- hosting ----------------------------------------------------------------------------------

def multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes, mime: str):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def host(media: Media, name: str) -> str:
    """Upload to Cloudinary and return the permanent public https URL."""
    cloud = os.environ["CLOUDINARY_CLOUD_NAME"]
    params = {"folder": "persona-agent", "public_id": name, "timestamp": str(int(time.time()))}
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params)) + os.environ["CLOUDINARY_API_SECRET"]
    fields = {**params, "api_key": os.environ["CLOUDINARY_API_KEY"], "signature": hashlib.sha1(to_sign.encode()).hexdigest()}
    body, ctype = multipart(fields, "file", f"{name}.png", media.data, media.mime_type)
    req = urllib.request.Request(
        f"https://api.cloudinary.com/v1_1/{cloud}/image/upload", data=body, headers={"Content-Type": ctype}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            media.url = json.loads(resp.read())["secure_url"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cloudinary upload failed ({e.code}): {e.read()[:300].decode(errors='replace')}")
    return media.url
