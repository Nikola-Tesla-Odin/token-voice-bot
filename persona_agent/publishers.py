"""Where utterances go. Each publisher takes (persona, utterance) and delivers it somewhere.

Configure network publishers with environment variables:
  discord   DISCORD_WEBHOOK_URL
  slack     SLACK_WEBHOOK_URL
  telegram  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  webhook   PERSONA_AGENT_WEBHOOK_URL  (receives the raw JSON record)
  buffer    BUFFER_API_KEY, optional BUFFER_CHANNEL_IDS (comma-separated; default: every channel),
            optional BUFFER_MODE (shareNow | addToQueue | shareNext; default shareNow)
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from .images import Media, multipart
from .llm import Utterance
from .persona import HOME, Persona


def render(
    persona: Persona, u: Utterance, translations: bool = True, signoff: bool = True, mentions: bool = False
) -> str:
    parts = [u.text, *([t.text for t in u.translations] if translations else [])]
    tail = " ".join(
        ([persona.format.signoff] if signoff and persona.format.signoff else [])
        + (u.mentions if mentions else [])
    )
    if tail:
        parts.append(tail)
    return "\n\n".join(parts)


def render_within(
    persona: Persona, u: Utterance, limit: int, length: Callable[[str], int] = len, mentions: bool = False
) -> str | None:
    """Richest rendering that fits a character limit: full → no translations → no signoff → bare text."""
    for kwargs in (
        {},
        {"translations": False},
        {"translations": False, "signoff": False},
        {"translations": False, "signoff": False, "mentions": False},
    ):
        text = render(persona, u, **{"mentions": mentions, **kwargs})
        if length(text) <= limit:
            return text
    return None


def x_length(text: str) -> int:
    """X's weighted count: Latin and common punctuation count 1, everything else (CJK, emoji) counts 2."""
    light = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))
    return sum(1 if any(lo <= ord(ch) <= hi for lo, hi in light) else 2 for ch in text)


def record(persona: Persona, u: Utterance) -> dict:
    return {
        "persona": persona.name,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topic": u.topic,
        "text": u.text,
        "translations": {t.language: t.text for t in u.translations},
        "mentions": u.mentions,
    }


def history_path(persona: Persona):
    return HOME / "history" / f"{persona.slug}.jsonl"


def _recent(persona: Persona, field: str, n: int) -> list[str]:
    path = history_path(persona)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(line)[field] for line in lines if line.strip()]


def recent_texts(persona: Persona, n: int = 20) -> list[str]:
    return _recent(persona, "text", n)


def recent_topics(persona: Persona, n: int = 12) -> list[str]:
    return _recent(persona, "topic", n)


# Web research costs more than writing, so only every Nth trending-mode post reacts to the news;
# the rest draw on the persona's own themes.
TREND_EVERY = int(os.environ.get("PERSONA_AGENT_TREND_EVERY", "3"))


def due_for_trend(persona: Persona) -> bool:
    path = history_path(persona)
    if TREND_EVERY <= 1 or not path.exists():
        return True
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()][-(TREND_EVERY - 1):]
    return not any(json.loads(l).get("trending") for l in lines)


def due_for_image(persona: Persona, every: int) -> bool:
    path = history_path(persona)
    if every <= 1 or not path.exists():
        return every >= 1
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()][-(every - 1):]
    return not any(json.loads(l).get("image") for l in lines)


# Automated @mentions are what X's spam rules target, so tag sparingly: at most one tagged post per N posts.
TAG_EVERY = int(os.environ.get("PERSONA_AGENT_TAG_EVERY", "4"))


def may_tag(persona: Persona) -> bool:
    if TAG_EVERY <= 0:
        return False
    path = history_path(persona)
    if not path.exists():
        return True
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()][-(TAG_EVERY - 1):] if TAG_EVERY > 1 else []
    return not any(json.loads(l).get("mentions") for l in lines)


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "persona-agent"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Set {name} to use this publisher.")
    return value


def console(persona: Persona, u: Utterance, media: Media | None = None) -> None:
    print(f"\n{render(persona, u, mentions=True)}\n")
    if media:
        print(f"[image] {media.url or f'{len(media.data) // 1024} KB, not hosted'}")


def history(persona: Persona, u: Utterance, meta: dict | None = None) -> None:
    path = history_path(persona)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**record(persona, u), **(meta or {})}, ensure_ascii=False) + "\n")


def discord(persona: Persona, u: Utterance, media: Media | None = None) -> None:
    _post_json(_env("DISCORD_WEBHOOK_URL"), {"username": persona.name, "content": render(persona, u)})


def slack(persona: Persona, u: Utterance, media: Media | None = None) -> None:
    _post_json(_env("SLACK_WEBHOOK_URL"), {"text": render(persona, u)})


def telegram(persona: Persona, u: Utterance, media: Media | None = None) -> None:
    token = _env("TELEGRAM_BOT_TOKEN")
    if media:  # Telegram takes the file directly; captions max out at 1024 chars
        body, ctype = multipart(
            {"chat_id": _env("TELEGRAM_CHAT_ID"), "caption": render_within(persona, u, 1024) or u.text[:1024]},
            "photo", "image.png", media.data, media.mime_type,
        )
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendPhoto", data=body, headers={"Content-Type": ctype}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        return
    _post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": _env("TELEGRAM_CHAT_ID"), "text": render(persona, u)},
    )


def webhook(persona: Persona, u: Utterance, media: Media | None = None) -> None:
    _post_json(_env("PERSONA_AGENT_WEBHOOK_URL"), {**record(persona, u), "image_url": media.url if media else ""})


# --- Buffer (https://developers.buffer.com) — one API for X, Bluesky, Threads, LinkedIn, Mastodon...

BUFFER_URL = "https://api.buffer.com"
# Post-text limits Buffer enforces per network (developers.buffer.com/guides/character-limits).
BUFFER_LIMITS = {"twitter": 280, "bluesky": 300, "threads": 500, "mastodon": 500, "pinterest": 500}
BUFFER_DEFAULT_LIMIT = 2000


def buffer_query(query: str, variables: dict | None = None) -> dict:
    req = urllib.request.Request(
        BUFFER_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_env('BUFFER_API_KEY')}",
            "User-Agent": "persona-agent",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise RuntimeError("; ".join(e.get("message", str(e)) for e in body["errors"]))
    return body["data"]


def buffer_channels() -> list[dict]:
    orgs = buffer_query("query { account { organizations { id name } } }")["account"]["organizations"]
    channels = []
    for org in orgs:
        data = buffer_query(
            "query($input: ChannelsInput!) { channels(input: $input) { id name displayName service isQueuePaused } }",
            {"input": {"organizationId": org["id"]}},
        )
        channels += data["channels"]
    return channels


def buffer(persona: Persona, u: Utterance, media: Media | None = None) -> None:
    wanted = [c.strip() for c in os.environ.get("BUFFER_CHANNEL_IDS", "").split(",") if c.strip()]
    channels = buffer_channels()
    if wanted:
        channels = [c for c in channels if c["id"] in wanted]
    if not channels:
        raise RuntimeError("No Buffer channels found. Connect one at publish.buffer.com, or check BUFFER_CHANNEL_IDS.")

    for ch in channels:
        label = f"{ch['service']}:{ch.get('displayName') or ch['name']}"
        service = ch["service"].lower()
        limit = next((v for k, v in BUFFER_LIMITS.items() if k in service), BUFFER_DEFAULT_LIMIT)
        on_x = "twitter" in service
        text = render_within(persona, u, limit, x_length if on_x else len, mentions=on_x)
        if text is None:
            print(f"[buffer] {label}: skipped, text is longer than {limit} chars even without extras")
            continue
        assets = [{"image": {"url": media.url}}] if media and media.url else []
        data = _buffer_create(ch["id"], text, assets)
        if "message" in data and assets:  # image rejected: still post the text
            print(f"[buffer] {label}: image rejected ({data['message']}); posting text only")
            data = _buffer_create(ch["id"], text, [])
        if "message" in data:
            print(f"[buffer] {label}: {data['message']}")
        else:
            print(f"[buffer] {label}: posted{' with image' if assets else ''} (id {data['post']['id']})")


def _buffer_create(channel_id: str, text: str, assets: list[dict]) -> dict:
    return buffer_query(
        """mutation($input: CreatePostInput!) {
             createPost(input: $input) {
               ... on PostActionSuccess { post { id dueAt } }
               ... on MutationError { message }
             }
           }""",
        {"input": {
            "text": text,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": os.environ.get("BUFFER_MODE", "shareNow"),
            "aiAssisted": True,
            "assets": assets,
        }},
    )["createPost"]


PUBLISHERS: dict[str, Callable[..., None]] = {
    "console": console,
    "buffer": buffer,
    "discord": discord,
    "slack": slack,
    "telegram": telegram,
    "webhook": webhook,
}


def publish(
    persona: Persona, u: Utterance, targets: list[str], meta: dict | None = None, media: Media | None = None
) -> None:
    """Always records to history (so the persona doesn't repeat itself), then fans out."""
    history(persona, u, {**(meta or {}), "image": media.url if media and media.url else bool(media)})
    for name in targets:
        if name not in PUBLISHERS:
            raise SystemExit(f"Unknown publisher '{name}'. Choose from: {', '.join(PUBLISHERS)}")
        try:
            PUBLISHERS[name](persona, u, media)
        except Exception as e:  # one failing channel shouldn't block the others
            print(f"[{name}] publish failed: {e}")
