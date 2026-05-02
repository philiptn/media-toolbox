#!/usr/bin/env python3
"""
Insert audio tracks from source files into matching no-audio encoded files.

Given a folder of encoded MKV files that are missing their audio tracks, and
a folder of source files (searched recursively), this script matches each
encoded file to its source by normalized filename and copies all audio tracks
from the source into the encoded file using mkvmerge.

Filename matching is case-insensitive and treats any run of non-alphanumeric
characters as a single space, so "The.Movie_2024.mkv" matches "The Movie 2024.mkv".

Run with no arguments and drag-and-drop the two folders into the prompts.

Requires: mkvtoolnix (mkvmerge).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m2ts", ".ts", ".avi", ".mpg", ".mpeg"}

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"


def check_dependencies():
    if not shutil.which("mkvmerge"):
        print(f"{RED}Error: 'mkvmerge' not found. Install mkvtoolnix:{RESET}")
        print("  sudo dnf install mkvtoolnix")
        sys.exit(1)


def clean_path(raw: str) -> str:
    """Handle drag-and-drop paths: strip whitespace, quotes, escaped spaces."""
    p = raw.strip()
    if (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
        p = p[1:-1]
    p = p.replace("\\ ", " ")
    return p


def prompt_dir(label: str) -> Path:
    while True:
        print(f"\n{CYAN}{label}{RESET}")
        raw = input("  > ")
        path = Path(clean_path(raw))
        if path.is_dir():
            return path
        print(f"  {RED}Not a valid directory: {path}{RESET}")


def normalize_key(name: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single spaces, strip."""
    stem = Path(name).stem
    return re.sub(r"[^a-z0-9]+", " ", stem.lower()).strip()


def find_media_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
    )


def index_sources(source_dir: Path) -> dict[str, Path]:
    """Build {normalized_key: Path}. Collisions are reported and dropped."""
    files = find_media_files(source_dir)
    index: dict[str, Path] = {}
    collisions: dict[str, list[Path]] = {}
    for f in files:
        key = normalize_key(f.name)
        if not key:
            continue
        if key in collisions:
            collisions[key].append(f)
        elif key in index:
            collisions[key] = [index.pop(key), f]
        else:
            index[key] = f
    for key, paths in collisions.items():
        print(f"{YELLOW}Collision on key '{key}' — skipping:{RESET}")
        for p in paths:
            print(f"  {DIM}{p}{RESET}")
    return index


def count_audio_tracks(path: Path) -> int | None:
    """Return audio track count via mkvmerge -J. None on identification failure."""
    try:
        result = subprocess.run(
            ["mkvmerge", "-J", str(path)],
            capture_output=True, text=True, timeout=30
        )
    except Exception as e:
        print(f"  {RED}mkvmerge -J failed: {e}{RESET}")
        return None
    if result.returncode != 0:
        return None
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return sum(1 for t in info.get("tracks", []) if t.get("type") == "audio")


def build_merge_cmd(encoded: Path, source: Path, output: Path) -> list[str]:
    return [
        "mkvmerge", "-o", str(output),
        "-A", str(encoded),
        "-D", "-S", "-M",
        "--no-chapters", "--no-global-tags", "--no-track-tags", "--no-buttons",
        str(source),
    ]


def merge_audio(encoded: Path, source: Path) -> bool:
    """Merge audio from source into encoded (in-place via tmp file)."""
    tmp = encoded.with_suffix(encoded.suffix + ".tmp")
    cmd = build_merge_cmd(encoded, source, tmp)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  {RED}mkvmerge failed (exit {result.returncode}){RESET}")
        if result.stderr.strip():
            print(f"  {DIM}{result.stderr.strip()}{RESET}")
        if tmp.exists():
            tmp.unlink()
        return False
    os.replace(tmp, encoded)
    return True


def main():
    check_dependencies()

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Insert audio into no-audio encoded files{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    print(f"{DIM}  Drag & drop folders into the terminal when prompted{RESET}")

    encoded_dir = prompt_dir("Encoded files folder (the no-audio ones):")
    source_dir = prompt_dir("Source files folder (the ones with audio, searched recursively):")
    print()

    print(f"{CYAN}Indexing source files...{RESET} {DIM}{source_dir}{RESET}")
    sources = index_sources(source_dir)
    print(f"  {DIM}{len(sources)} unique source key(s){RESET}\n")

    encoded_files = [
        p for p in find_media_files(encoded_dir) if p.suffix.lower() == ".mkv"
    ]
    if not encoded_files:
        print(f"{YELLOW}No .mkv files found in {encoded_dir}{RESET}")
        sys.exit(1)

    print(f"{CYAN}Processing encoded files...{RESET} {DIM}{encoded_dir}{RESET}\n")

    merged = 0
    skipped_no_audio = 0
    not_found = 0
    failed = 0

    for enc in encoded_files:
        rel = enc.relative_to(encoded_dir)
        key = normalize_key(enc.name)
        src = sources.get(key)

        if src is None:
            print(f"  {YELLOW}[NO MATCH]{RESET} {rel}")
            not_found += 1
            continue

        n_audio = count_audio_tracks(src)
        if n_audio is None:
            print(f"  {RED}[ID FAIL]{RESET}  {rel}  {DIM}← could not identify {src.name}{RESET}")
            failed += 1
            continue
        if n_audio == 0:
            print(f"  {YELLOW}[NO AUDIO]{RESET} {rel}  {DIM}← {src.name} has no audio{RESET}")
            skipped_no_audio += 1
            continue

        if merge_audio(enc, src):
            print(f"  {GREEN}[OK]{RESET}       {rel}  ←  {src.name}  {DIM}({n_audio} audio track(s)){RESET}")
            merged += 1
        else:
            failed += 1

    print()
    print(f"{BOLD}Summary:{RESET}")
    print(f"  {GREEN}merged:{RESET}        {merged}")
    print(f"  {YELLOW}no match:{RESET}      {not_found}")
    print(f"  {YELLOW}no audio in src:{RESET} {skipped_no_audio}")
    print(f"  {RED}failed:{RESET}        {failed}")

    if not_found or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
