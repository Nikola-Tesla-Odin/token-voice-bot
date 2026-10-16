"""persona-agent command line.

  persona-agent list
  persona-agent show confucius
  persona-agent create "Marcus Aurelius" [--notes "..."] [--lang English Latin]
  persona-agent speak confucius [--topic "patience" | --trending] [--publish console discord]
  persona-agent chat confucius
  persona-agent run confucius --every 24h [--publish console telegram]
  persona-agent buffer-channels

Keys (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, BUFFER_API_KEY, ...) can live in ~/.persona-agent/.env.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time

import anthropic

from . import budget, card, images, llm, publishers
from .persona import list_personas, load_persona, save_persona

def parse_interval(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smhd])", text.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 30m, 6h, 1d")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def cmd_list(args) -> None:
    personas = list_personas()
    if not personas:
        print('No personas yet. Try: persona-agent create "Confucius"')
        return
    for slug, path in personas.items():
        p = load_persona(str(path))
        print(f"{slug:<20} {p.tagline}")


def cmd_show(args) -> None:
    p = load_persona(args.persona)
    print(p.system_prompt())
    print(f"\nThemes: {', '.join(p.themes)}")
    print(f"Languages: {', '.join(p.languages)}")
    print(f"Format: {p.format.kind}, ≤{p.format.max_words} words, signoff {p.format.signoff!r}")


def cmd_create(args) -> None:
    print(f"Designing persona for {args.who!r}...")
    p = llm.design_persona(args.who, args.notes or "", args.lang)
    path = save_persona(p)
    print(f"Saved {path}\nEdit it freely, then: persona-agent speak {p.slug}")


def speak_once(persona, topic: str | None, targets: list[str], trending: bool = False) -> None:
    budget.ensure_room()
    research, researched = "", False
    if trending and not topic and publishers.due_for_trend(persona) and budget.can_afford_research():
        researched = True  # counts toward the trend cadence even if the story gets filtered out
        research = llm.research_trend(persona, publishers.recent_topics(persona))
        m = re.search(r"^TOPIC:\s*(.+)$", research, re.M)
        if llm.is_sensitive(research):
            print(f"[trending] skipped sensitive story: {m.group(1).strip() if m else research[:80]}")
            research = ""
        else:
            topic = m.group(1).strip() if m else "today's news"
            print(f"[trending] {topic}")
    topic = topic or random.choice(persona.themes)
    u = llm.speak(persona, topic, publishers.recent_texts(persona), research)
    if u.mentions and not publishers.may_tag(persona):
        print(f"[tagging] skipped {' '.join(u.mentions)} (tagged recently)")
        u.mentions = []
    media = make_image(persona, u, targets)
    publishers.publish(persona, u, targets, meta={"trending": researched}, media=media)
    print(f"[budget] ${budget.spent():.3f} of ${budget.BUDGET_USD:.2f} spent this month")


def make_image(persona, u, targets: list[str]):
    """Attach an image to every Nth post: a free quote card, or a Gemini illustration within its budget.
    Any failure just means a text-only post."""
    if not (images.enabled() and publishers.due_for_image(persona, images.IMAGE_EVERY)):
        return None
    try:
        if images.MODE == "gemini":
            if not (u.image_scene and images.can_afford()):
                print(f"[image] skipped: no scene, or ${images.BUDGET_USD:.2f} image budget reached")
                return None
            media = images.generate(persona, u.image_scene)
            print(f"[image] ${images.spent():.2f} of ${images.BUDGET_USD:.2f} image budget spent this month")
        else:
            media = card.render(persona, u.text)
        if "buffer" in targets:
            if not images.hosting_configured():
                print("[image] X post goes without image: set the CLOUDINARY_ keys to host it", file=sys.stderr)
            else:
                images.host(media, f"{persona.slug}-{int(time.time())}")
        return media
    except Exception as e:
        print(f"[image] skipped: {e}", file=sys.stderr)
        return None


def cmd_speak(args) -> None:
    speak_once(load_persona(args.persona), args.topic, args.publish, args.trending)


def cmd_chat(args) -> None:
    p = load_persona(args.persona)
    print(f"Chatting with {p.name}. Ctrl-D or 'exit' to leave.\n")
    history: list[dict] = []
    while True:
        try:
            line = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line.lower() in {"exit", "quit"}:
            return
        if not line:
            continue
        history.append({"role": "user", "content": line})
        print(f"{p.name} › ", end="", flush=True)
        reply = llm.chat(p, history)
        history.append({"role": "assistant", "content": reply})


def cmd_buffer_channels(args) -> None:
    for c in publishers.buffer_channels():
        paused = "  (queue paused)" if c.get("isQueuePaused") else ""
        print(f"{c['id']}  {c['service']:<12} {c.get('displayName') or c['name']}{paused}")


def cmd_run(args) -> None:
    p = load_persona(args.persona)
    print(f"{p.name} will speak every {args.every}s to: {', '.join(args.publish)}. Ctrl-C to stop.")
    while True:
        try:
            speak_once(p, args.topic, args.publish, args.trending)
        except (anthropic.APIError, RuntimeError) as e:  # keep the loop alive through transient failures
            print(f"[run] skipped this round: {e}", file=sys.stderr)
        time.sleep(args.every)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="persona-agent", description="An AI agent that speaks as any personality.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List available personas").set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="Print a persona's system prompt and settings")
    s.add_argument("persona")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("create", help="Generate a new persona from a name or description")
    s.add_argument("who", help="e.g. 'Marcus Aurelius' or 'a sarcastic medieval blacksmith'")
    s.add_argument("--notes", help="Extra direction for the voice")
    s.add_argument("--lang", nargs="+", help="Languages to post in, primary first")
    s.set_defaults(fn=cmd_create)

    for name, fn, help_ in [
        ("speak", cmd_speak, "Produce one utterance and publish it"),
        ("run", cmd_run, "Speak on a schedule, forever"),
    ]:
        s = sub.add_parser(name, help=help_)
        s.add_argument("persona", help="Persona name or path to a YAML file")
        s.add_argument("--topic", help="What to speak about (default: random theme)")
        s.add_argument(
            "--trending", action="store_true",
            help="Search the web for a story trending now on X / in the news and react to it (tags handles on X)",
        )
        s.add_argument(
            "--publish", nargs="+", default=["console"], choices=list(publishers.PUBLISHERS),
            help="Where to send it (default: console)",
        )
        if name == "run":
            s.add_argument("--every", type=parse_interval, default="24h", help="Interval, e.g. 30m, 6h, 1d")
        s.set_defaults(fn=fn)

    s = sub.add_parser("chat", help="Talk with a persona interactively")
    s.add_argument("persona")
    s.set_defaults(fn=cmd_chat)

    sub.add_parser("buffer-channels", help="List your Buffer channels and their IDs").set_defaults(
        fn=cmd_buffer_channels
    )

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except anthropic.AuthenticationError:
        sys.exit("Claude API auth failed. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.APIError as e:
        sys.exit(f"Claude API error: {e}")
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
