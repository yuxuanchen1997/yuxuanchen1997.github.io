#!/usr/bin/env python3
"""Generate and publish one bilingual surrealist story using the Codex CLI.

Designed for an unattended daily cron job. It generates both languages, builds
both Hexo sites, commits their content, and pushes the current Git branch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
DICTIONARY_PATH = Path(__file__).resolve().with_name("story_words.txt")
HEXO_DIR = ROOT / "stories"
ENGLISH_DIR = HEXO_DIR / "source" / "texts"
CHINESE_HEXO_DIR = ROOT / "stories_ch"
CHINESE_DIR = CHINESE_HEXO_DIR / "source" / "texts"
ENGLISH_INDEX = HEXO_DIR / "source" / "index.md"
CHINESE_INDEX = CHINESE_HEXO_DIR / "source" / "index.md"
ENGLISH_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-en\.txt$")
WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
MIN_WORDS = 300
MAX_WORDS = 400
MIN_DICTIONARY_WORDS = 1000
DISALLOWED_SEED_WORDS = {
    "alcohol", "battle", "beer", "blood", "body", "bomb", "cemetery",
    "church", "coffin", "crime", "dagger", "dead", "death", "disease",
    "drug", "dying", "ethnic", "female", "flesh", "funeral", "ghost",
    "grave", "gun", "hate", "husband", "knife", "lover", "lunar", "male",
    "moon", "mosque", "mourning", "naked", "nazi", "nude", "penis",
    "pistol", "politics", "porn", "prison", "race", "racial", "racism",
    "racist", "rape", "religion", "rifle", "romance", "sex", "sexism",
    "sexist", "sexual", "sexy", "slur", "sword", "temple", "tobacco",
    "vagina", "violence", "vodka", "war", "weapon", "whiskey", "wife",
    "wine",
}

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
        default=os.environ.get("CODEX_MODEL", "gpt-5.6-sol"),
        help="Codex model (default: gpt-5.6-sol; or set CODEX_MODEL)",
    )
    return parser.parse_args()


def select_seed_words(dictionary_path: Path = DICTIONARY_PATH, count: int = 3) -> list[str]:
    """Select distinct seed words using fresh randomness from the operating system."""
    try:
        words = [
            line.strip().lower()
            for line in dictionary_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError as error:
        raise RuntimeError(f"could not read seed dictionary: {dictionary_path}") from error

    unique_words = list(dict.fromkeys(words))
    disallowed = sorted(set(unique_words) & DISALLOWED_SEED_WORDS)
    if disallowed:
        raise RuntimeError(
            "seed dictionary contains disallowed words: " + ", ".join(disallowed)
        )
    if len(unique_words) < MIN_DICTIONARY_WORDS:
        raise RuntimeError(
            f"seed dictionary needs at least {MIN_DICTIONARY_WORDS} distinct words; "
            f"found {len(unique_words)}"
        )
    if len(unique_words) < count:
        raise RuntimeError(
            f"seed dictionary needs at least {count} distinct words; found {len(unique_words)}"
        )
    return secrets.SystemRandom().sample(unique_words, count)


def story_prompt(seed_words: list[str]) -> str:
    seeds = ", ".join(seed_words)
    return f"""Write an original short story.

Requirements:
- Use these three randomly selected seed words as central creative constraints:
  {seeds}
- Let the seeds determine the story's objects, actions, setting, or conflict. Do
  not merely mention them in passing.
- The English story must be fictional literary prose in a surrealist style.
- The English story body must contain between {MIN_WORDS} and {MAX_WORDS} words.
- Give it a short, evocative English title.
- Then provide a faithful, polished Simplified Chinese translation, including a
  translated title. Preserve the imagery, tone, paragraph breaks, and ambiguity.
- Make this story self-contained. Do not mention these instructions, its word
  count, AI, Codex, or the translation process.
- Avoid overused gothic or cosmic motifs, especially the moon, funerals, death,
  graves, cemeteries, ghosts, and mourning.
- Keep the story suitable for a general audience. Do not introduce sexual
  content, gender or racial stereotypes, discrimination, slurs, or hate themes.
- Avoid Markdown headings and code fences inside all four string values.
- Return only the object required by the supplied output schema.
"""


def generate_story(model: str) -> dict[str, str]:
    ENGLISH_DIR.mkdir(parents=True, exist_ok=True)
    seed_words = select_seed_words()
    print(f"Story seeds: {', '.join(seed_words)}")
    with tempfile.TemporaryDirectory(prefix="daily-story-", dir=ENGLISH_DIR) as temp:
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
                input=story_prompt(seed_words),
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


def run_checked(command: list[str], cwd: Path) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed with exit code {result.returncode}"
        )


def build_site(site_dir: Path) -> None:
    if site_dir == CHINESE_HEXO_DIR:
        shutil.copytree(
            HEXO_DIR / "themes" / "stories",
            CHINESE_HEXO_DIR / "themes" / "stories",
            dirs_exist_ok=True,
        )
    run_checked(["npm", "run", "build"], site_dir)
    public_dir = site_dir / "public"
    if not (public_dir / "index.html").is_file():
        raise RuntimeError(f"Hexo did not create {public_dir / 'index.html'}")
    shutil.copytree(public_dir, site_dir, dirs_exist_ok=True)


def commit_and_push(publication_date: str) -> None:
    paths = [
        "stories/source/index.md",
        "stories/source/texts",
        "stories/index.html",
        "stories/texts",
        "stories_ch/source/index.md",
        "stories_ch/source/texts",
        "stories_ch/index.html",
        "stories_ch/texts",
    ]
    run_checked(["git", "add", "--", *paths], ROOT)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False
    )
    if staged.returncode == 0:
        print("Nothing changed; skipping commit and push.")
        return
    if staged.returncode != 1:
        raise RuntimeError("could not inspect staged Git changes")
    run_checked(
        ["git", "commit", "-m", f"Publish daily story {publication_date}"], ROOT
    )
    run_checked(["git", "push", "origin", "HEAD"], ROOT)


def read_title(path: Path) -> str:
    with path.open(encoding="utf-8") as story_file:
        return story_file.readline().strip() or path.stem


def story_entries() -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for english_path in ENGLISH_DIR.glob("*-en.txt"):
        match = ENGLISH_FILE_RE.fullmatch(english_path.name)
        if not match:
            continue
        date = match.group(1)
        chinese_path = CHINESE_DIR / f"{date}-zh.txt"
        if chinese_path.is_file():
            entries.append((date, read_title(english_path), read_title(chinese_path)))
    entries.sort(reverse=True)
    return entries


def build_index(language: str) -> str:
    entries = story_entries()
    if language == "en":
        title = "Daily Stories"
        rows = "\n".join(
            f"- {date} — [{english_title}](texts/{date}-en.txt)"
            for date, english_title, _ in entries
        )
        empty = "No stories have been published yet."
    else:
        title = "每日故事"
        rows = "\n".join(
            f"- {date} — [{chinese_title}](texts/{date}-zh.txt)"
            for date, _, chinese_title in entries
        )
        empty = "暂无故事"
    if not rows:
        rows = empty

    return f"""---
title: {title}
layout: page
---

{rows}
"""


def main() -> int:
    cron_paths = [
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    os.environ["PATH"] = os.pathsep.join(cron_paths + [os.environ.get("PATH", "")])

    lock_file = (Path(tempfile.gettempdir()) / "yuxuan-daily-story.lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another daily story process is already running; exiting.")
        return 0

    args = parse_args()
    date = args.date.isoformat()
    english_path = ENGLISH_DIR / f"{date}-en.txt"
    chinese_path = CHINESE_DIR / f"{date}-zh.txt"

    if not args.force and english_path.exists() and chinese_path.exists():
        print(f"Using the existing story for {date} and resuming publication.")
    elif not args.force and (english_path.exists() or chinese_path.exists()):
        raise RuntimeError(
            f"only one language exists for {date}; use --force to replace the pair"
        )
    else:
        story = generate_story(args.model)
        atomic_write(
            english_path, f"{story['english_title']}\n\n{story['english_story']}\n"
        )
        atomic_write(
            chinese_path, f"{story['chinese_title']}\n\n{story['chinese_story']}\n"
        )
    atomic_write(ENGLISH_INDEX, build_index("en"))
    atomic_write(CHINESE_INDEX, build_index("zh"))
    build_site(HEXO_DIR)
    build_site(CHINESE_HEXO_DIR)
    commit_and_push(date)
    print(f"Published {english_path.relative_to(ROOT)} and {chinese_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"daily_story.py: {error}", file=sys.stderr)
        raise SystemExit(1)
