#!/usr/bin/env python3
"""Generate and publish one bilingual surrealist story using the Codex CLI.

Designed for an unattended daily cron job. Output is written to the Hexo source
tree as ``stories/source/texts/YYYY-MM-DD-{en,zh}.txt`` and ``index.md``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


HEXO_DIR = Path(__file__).resolve().parent.parent
ROOT = HEXO_DIR.parent
STORIES_DIR = HEXO_DIR / "source" / "texts"
INDEX_SOURCE = HEXO_DIR / "source" / "index.md"
ENGLISH_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-en\.txt$")
WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
MIN_WORDS = 300
MAX_WORDS = 400

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "english_title": {"type": "string"},
        "english_story": {"type": "string"},
        "chinese_title": {"type": "string"},
        "chinese_story": {"type": "string"},
    },
    "required": [
        "english_title",
        "english_story",
        "chinese_title",
        "chinese_story",
    ],
    "additionalProperties": False,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate today's English/Chinese surrealist short story."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=dt.date.today(),
        help="publication date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace files for an existing date"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CODEX_MODEL"),
        help="Codex model override (or set CODEX_MODEL)",
    )
    return parser.parse_args()


def prompt_for(publication_date: dt.date) -> str:
    return f"""Write an original short story for {publication_date.isoformat()}.

Requirements:
- The English story must be fictional literary prose in a surrealist style.
- The English story body must contain between {MIN_WORDS} and {MAX_WORDS} words.
- Give it a short, evocative English title.
- Then provide a faithful, polished Simplified Chinese translation, including a
  translated title. Preserve the imagery, tone, paragraph breaks, and ambiguity.
- Make this story self-contained. Do not mention these instructions, its word
  count, AI, Codex, or the translation process.
- Avoid Markdown headings and code fences inside all four string values.
- Return only the object required by the supplied output schema.
"""


def generate_story(publication_date: dt.date, model: str | None) -> dict[str, str]:
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="daily-story-", dir=STORIES_DIR) as temp:
        temp_dir = Path(temp)
        schema_path = temp_dir / "schema.json"
        response_path = temp_dir / "response.json"
        schema_path.write_text(
            json.dumps(OUTPUT_SCHEMA, ensure_ascii=False), encoding="utf-8"
        )

        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(response_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")

        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                input=prompt_for(publication_date),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError("codex is not installed or is not on PATH") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("codex did not finish within 10 minutes") from error

        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"codex failed with exit code {result.returncode}: {details}")

        try:
            story = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("codex did not produce valid structured output") from error

    for key in OUTPUT_SCHEMA["required"]:
        if not isinstance(story.get(key), str) or not story[key].strip():
            raise RuntimeError(f"codex returned an empty or invalid {key}")
        story[key] = story[key].strip()

    word_count = len(WORD_RE.findall(story["english_story"]))
    if not MIN_WORDS <= word_count <= MAX_WORDS:
        raise RuntimeError(
            f"English story is {word_count} words; expected {MIN_WORDS}-{MAX_WORDS}"
        )
    return story


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def read_title(path: Path) -> str:
    with path.open(encoding="utf-8") as story_file:
        return story_file.readline().strip() or path.stem


def build_index() -> str:
    entries: list[tuple[str, str, str]] = []
    for english_path in STORIES_DIR.glob("*-en.txt"):
        match = ENGLISH_FILE_RE.fullmatch(english_path.name)
        if not match:
            continue
        date = match.group(1)
        chinese_path = STORIES_DIR / f"{date}-zh.txt"
        if chinese_path.is_file():
            entries.append((date, read_title(english_path), read_title(chinese_path)))
    entries.sort(reverse=True)

    rows = "\n".join(
        f"- {date} — [{english_title}](texts/{date}-en.txt) · "
        f"[{chinese_title}](texts/{date}-zh.txt)"
        for date, english_title, chinese_title in entries
    )
    if not rows:
        rows = "No stories have been published yet."

    return f"""---
title: Daily Stories
layout: page
---

Surrealist fiction in English and Simplified Chinese.

{rows}
"""


def main() -> int:
    args = parse_args()
    date = args.date.isoformat()
    english_path = STORIES_DIR / f"{date}-en.txt"
    chinese_path = STORIES_DIR / f"{date}-zh.txt"

    if not args.force and (english_path.exists() or chinese_path.exists()):
        print(f"A story for {date} already exists; use --force to replace it.", file=sys.stderr)
        return 2

    story = generate_story(args.date, args.model)
    atomic_write(
        english_path, f"{story['english_title']}\n\n{story['english_story']}\n"
    )
    atomic_write(
        chinese_path, f"{story['chinese_title']}\n\n{story['chinese_story']}\n"
    )
    atomic_write(INDEX_SOURCE, build_index())
    print(f"Published {english_path.relative_to(ROOT)} and {chinese_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"daily_story.py: {error}", file=sys.stderr)
        raise SystemExit(1)
