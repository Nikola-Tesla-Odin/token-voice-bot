"""Persona definitions: the YAML schema, loading, and system-prompt construction."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

HOME = Path(os.environ.get("PERSONA_AGENT_HOME", Path.home() / ".persona-agent"))
BUNDLED_DIR = Path(__file__).parent / "personas"
USER_DIR = HOME / "personas"


class OutputFormat(BaseModel):
    kind: str = Field(
        "quote",
        description="What the persona produces each time: quote, aphorism, thought, tweet, poem, advice...",
    )
    max_words: int = Field(60, description="Hard upper bound on length of each utterance.")
    max_chars: int = Field(
        260, description="Character cap on the primary-language text; 260 fits an X post with a short signoff."
    )
    signoff: str = Field("", description="Optional line appended when published, e.g. '— IConfucius'.")
    plain_language: bool = Field(
        True, description="Use simple everyday words and short sentences, while keeping the persona's voice."
    )


class Persona(BaseModel):
    """Everything needed to make the model speak as someone. One YAML file = one persona."""

    name: str = Field(description="Display name, e.g. 'Confucius'.")
    tagline: str = Field(description="One line describing who this is.")
    bio: str = Field(description="A short paragraph: era, life, worldview. Grounds the voice.")
    voice: list[str] = Field(description="Concrete style rules: diction, rhythm, imagery, tone.")
    themes: list[str] = Field(description="Topics this persona naturally speaks about; used when no topic is given.")
    examples: list[str] = Field(
        default_factory=list,
        description="Short sample lines in the persona's voice (original, not real quotations).",
    )
    avoid: list[str] = Field(default_factory=list, description="Things the persona never does or says.")
    languages: list[str] = Field(
        default_factory=lambda: ["English"],
        description="Languages to produce each utterance in. First is primary.",
    )
    format: OutputFormat = Field(default_factory=OutputFormat)
    portrait: str = Field(
        "", description="Path to a portrait image for quote cards. Leave empty; the user adds one later."
    )
    context: str = Field(
        "", description="What this persona speaks for, e.g. a project or token. Leave empty unless asked."
    )

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def system_prompt(self) -> str:
        lines = [
            f"You are {self.name} — {self.tagline}",
            "",
            "## Who you are",
            self.bio.strip(),
            "",
            "## How you speak",
            *[f"- {v}" for v in self.voice],
        ]
        if self.context:
            lines += ["", "## What you speak for", self.context.strip()]
        if self.avoid:
            lines += ["", "## Never", *[f"- {a}" for a in self.avoid]]
        if self.examples:
            lines += [
                "",
                "## Lines in your voice (for tone only; never repeat them verbatim)",
                *[f"- {e}" for e in self.examples],
            ]
        lines += [
            "",
            "## Rules",
            f"- Stay fully in character as {self.name}. Never mention being an AI or a language model.",
            "- Write original lines. Do not present invented words as historical quotations.",
            "- Apply your perspective to the modern topic you're given; don't refuse it as anachronistic.",
        ]
        return "\n".join(lines)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def persona_dirs() -> list[Path]:
    """Search order: ./personas, then ~/.persona-agent/personas, then bundled."""
    return [Path.cwd() / "personas", USER_DIR, BUNDLED_DIR]


def list_personas() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in reversed(persona_dirs()):  # earlier dirs override later ones
        if d.is_dir():
            for p in sorted(d.glob("*.yaml")):
                found[p.stem] = p
    return dict(sorted(found.items()))


def load_persona(name_or_path: str) -> Persona:
    path = Path(name_or_path)
    if not path.is_file():
        available = list_personas()
        key = slugify(name_or_path)
        if key not in available:
            names = ", ".join(available) or "(none)"
            raise SystemExit(
                f"No persona '{name_or_path}'. Available: {names}\n"
                f"Create one with: persona-agent create \"{name_or_path}\""
            )
        path = available[key]
    with path.open(encoding="utf-8") as f:
        persona = Persona.model_validate(yaml.safe_load(f))
    # A relative portrait path is relative to the persona's YAML file, so personas travel with their assets.
    if persona.portrait and not Path(persona.portrait).expanduser().is_absolute():
        persona.portrait = str(path.parent / persona.portrait)
    return persona


def save_persona(persona: Persona, directory: Path = USER_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{persona.slug}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(persona.model_dump(), f, sort_keys=False, allow_unicode=True, width=100)
    return path
