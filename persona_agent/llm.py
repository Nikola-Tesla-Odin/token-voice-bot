"""All Claude calls live here: researching trends, speaking as a persona, chatting, designing personas."""

from __future__ import annotations

import os
import re
from datetime import date

import anthropic
from pydantic import BaseModel, Field

from . import budget
from .persona import Persona

MODEL = os.environ.get("PERSONA_AGENT_MODEL", "claude-opus-5-5")
# Short creative lines don't need deep reasoning; persona design gets more.
SPEAK_EFFORT = os.environ.get("PERSONA_AGENT_EFFORT", "medium")
CREATE_EFFORT = "high"

# On a safety decline, let the API re-run the request on its recommended fallback model.
# Only some models support server-side fallbacks (Haiku doesn't).
FALLBACK = (
    {
        "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
        "extra_body": {"fallbacks": "default"},
    }
    if MODEL in {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
    else {}
)
# Each search costs money; one is usually enough to see what's trending today.
SEARCHES_PER_TREND = int(os.environ.get("PERSONA_AGENT_SEARCHES", "1"))
# Haiku only supports the basic web search tool; newer Opus/Sonnet get dynamic filtering.
WEB_SEARCH_TOOL = "web_search_20250305" if "haiku" in MODEL else "web_search_20260209"

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class Translation(BaseModel):
    language: str
    text: str


class Utterance(BaseModel):
    topic: str = Field(description="The topic actually addressed, in a few words.")
    text: str = Field(description="The utterance in the primary language.")
    translations: list[Translation] = Field(
        default_factory=list, description="The same utterance rendered in each additional language."
    )
    mentions: list[str] = Field(
        default_factory=list,
        description="X handle to tag (e.g. '@NASA'), taken only from the research notes, if any. 0 or 1.",
    )
    image_scene: str = Field(
        "",
        description="One sentence describing an illustration for this post: a scene from your own world and era "
        "blended with the modern subject of the post. Concrete and visual; no text in the picture.",
    )


HANDLE = re.compile(r"@\w{1,15}")


def research_trend(persona: Persona, recent_topics: list[str]) -> str:
    """Search the web for one trending story the persona could comment on. Returns research notes."""
    prompt = (
        f"Today is {date.today():%B %d, %Y}. Use web search to find what is trending RIGHT NOW on X and in "
        "world news (last 24 hours). Pick ONE story that "
        f"{persona.name} ({persona.tagline}) could say something meaningful about. Prefer big, broadly "
        "discussed stories about science, technology, space, nature, sports, culture, or business. NEVER pick "
        "war, military, armed conflict, politics, governments, elections, sanctions, crime, disasters, "
        "deaths, or anything graphic.\n"
        + (f"Skip these recently covered topics: {'; '.join(recent_topics)}\n" if recent_topics else "")
        + "\nReply with exactly these lines, nothing else:\n"
        "TOPIC: <the story in a few words>\n"
        "SUMMARY: <2-3 plain sentences of what happened>\n"
        "HANDLES: <at most 1 official X handle of the main ORGANIZATION in the story (agency, company, team, "
        "institution; like @NASA), never an individual person, and ONLY if you saw that exact handle in your "
        "search results; otherwise 'none'>"
    )
    messages = [{"role": "user", "content": prompt}]
    for _ in range(5):  # resume server-side search loops that pause
        response = client().messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": SPEAK_EFFORT},
            tools=[{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": SEARCHES_PER_TREND}],
            messages=messages,
            **FALLBACK,
        )
        if response.stop_reason != "pause_turn":
            break
        budget.record(response)
        messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": response.content}]
    _check(response)
    return "".join(b.text for b in response.content if b.type == "text").strip()


# Hard filter on top of the prompt: small models sometimes ignore "avoid" instructions.
SENSITIVE = re.compile(
    r"\b(war|wars|military|army|troops|soldier\w*|missile\w*|drone strike\w*|attack\w*|bomb\w*|invasion|"
    r"ceasefire|sanction\w*|ukrain\w*|russia\w*|kremlin|putin|zelensk\w*|gaza|israel\w*|hamas|iran\w*|"
    r"taliban|terror\w*|election\w*|vote|voting|ballot\w*|president|congress|senate|parliament|"
    r"democrat\w*|republican\w*|trump|biden|government|minister|politic\w*|shooting|killed|kill\w*|"
    r"dead|death\w*|died|murder\w*|victim\w*|crash\w*|earthquake|hurricane|flood\w*|wildfire\w*|"
    r"disaster\w*|refugee\w*|protest\w*|riot\w*)\b",
    re.I,
)


def is_sensitive(research: str) -> bool:
    return bool(SENSITIVE.search(research))


def _check(response) -> None:
    budget.record(response)
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None) if response.stop_details else None
        raise RuntimeError(f"Claude declined this request{': ' + detail if detail else '.'}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Response was cut off (max_tokens). Try again or shorten the request.")


def speak(persona: Persona, topic: str, recent: list[str], research: str = "") -> Utterance:
    """Produce one utterance (quote/thought/post...) in the persona's voice.

    With `research` notes (from research_trend), the line reacts to that news story and may tag its handles.
    """
    fmt = persona.format
    primary, *others = persona.languages
    task = [f"Write one original {fmt.kind} about: {topic}"]
    if research:
        task += [
            "This is today's news. Research notes:",
            research,
            "- A stranger scrolling past must understand it with no other context. So, in your own voice and "
            "first person, first say plainly what happened (name the key thing, e.g. 'Today Tesla showed a car "
            "that lifts off the ground'), then give your one clear thought about it. End on your thought, "
            "not on a fact.",
            "- Stick to facts in the notes; don't add details. Speak as yourself, not like a reporter or narrator.",
            "- For mentions: use only handles listed under HANDLES above (none if it says none).",
        ]
    else:
        task += [
            "- Make one clear point a stranger gets on first read: say the idea plainly, and use at most one "
            "simple image to support it, not a chain of metaphors.",
            "- Leave mentions empty.",
        ]
    if fmt.plain_language:
        task.append(
            "- Use simple, everyday words a 12-year-old would understand and short sentences. "
            "Keep your character through ideas and images, not old-fashioned or fancy vocabulary."
        )
    task += [
        f"- At most {fmt.max_words} words and {fmt.max_chars} characters (count carefully; it must fit a social post).",
        f"- Primary language: {primary}.",
    ]
    if others:
        task.append(
            f"- Also give faithful translations into: {', '.join(others)}, keeping your voice in each."
        )
    # The persona's own sample lines count too, or the model tends to echo them.
    if avoid := [*persona.examples, *recent]:
        task.append("- Find a fresh image and idea. Don't repeat or closely paraphrase any of these:")
        task += [f"  • {r}" for r in avoid]

    messages = [{"role": "user", "content": "\n".join(task)}]
    for _ in range(2):  # one retry if the line overshoots the character cap
        response = client().messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=persona.system_prompt(),
            output_config={"effort": SPEAK_EFFORT},
            messages=messages,
            output_format=Utterance,
            **FALLBACK,
        )
        _check(response)
        u = response.parsed_output
        # Only tag handles the web research actually surfaced, never ones the model made up.
        allowed = {h.lower() for h in HANDLE.findall(research)}
        u.mentions = [m for m in ("@" + m.lstrip("@") for m in u.mentions) if m.lower() in allowed][:1]
        if len(u.text) <= fmt.max_chars:
            return u
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": f"That is {len(u.text)} characters; rewrite it in under {fmt.max_chars}."},
        ]
    return u


def chat(persona: Persona, history: list[dict]) -> str:
    """One conversational turn in character. `history` is the full message list, mutated by the caller."""
    with client().messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=persona.system_prompt()
        + "\n\nYou are now in a live conversation. Reply conversationally, briefly, in character.",
        output_config={"effort": SPEAK_EFFORT},
        messages=history,
        **FALLBACK,
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
        response = stream.get_final_message()
    print()
    _check(response)
    return "".join(b.text for b in response.content if b.type == "text")


def design_persona(who: str, notes: str = "", languages: list[str] | None = None) -> Persona:
    """Turn a name or short description ('Marcus Aurelius', 'a grumpy pirate captain') into a full persona."""
    prompt = (
        f"Design a persona definition for an AI agent that speaks as: {who}\n"
        + (f"Extra direction from the user: {notes}\n" if notes else "")
        + "\nThe agent will post short original lines in this voice, and chat in character. Make it vivid "
        "and specific enough that the voice is unmistakable:\n"
        "- bio: 3-5 sentences grounding era, life, and worldview (for fictional or archetypal characters, invent "
        "a coherent backstory).\n"
        "- voice: 5-8 concrete style rules (sentence shape, vocabulary, imagery, rhetorical habits, tone).\n"
        "- themes: 8-12 topics they'd naturally address, mixing timeless and modern ones.\n"
        "- examples: 4-6 ORIGINAL sample lines in their voice. Never present real quotations.\n"
        "- avoid: 3-6 things that would break character.\n"
        f"- languages: {languages or 'English, plus their native language if it differs'}.\n"
        "- format: pick the kind of short output that suits them (quote, aphorism, verse, rant, tweet...), "
        "a sensible max_words, and a signoff like '— Name'."
    )
    response = client().messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": CREATE_EFFORT},
        messages=[{"role": "user", "content": prompt}],
        output_format=Persona,
        **FALLBACK,
    )
    _check(response)
    return response.parsed_output
