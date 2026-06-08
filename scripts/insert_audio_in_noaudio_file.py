#!/usr/bin/env python3
"""
Insert audio tracks from source files into matching no-audio encoded files.

Given a folder of encoded MKV files that are missing their audio tracks, and
a folder of source files (searched recursively), this script matches each
encoded file to its source by normalized filename and copies all audio tracks
from the source into the encoded file using mkvmerge.

Filename matching is case-insensitive and treats any run of non-alphanumeric
characters as a single space, so "The.Movie_2024.mkv" matches "The Movie 2024.mkv".

When several source files share a filename (e.g. "01.mkv" under different
folders), the collision is resolved by comparing parent-folder names, walking
up from the deepest folder until a single source remains. If it still can't be
resolved (a genuine tie), the encoded file is reported as ambiguous and skipped.

Requires: mkvtoolnix (mkvmerge).
"""

import argparse
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


def _normalize_text(text: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single spaces, strip."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_key(name: str) -> str:
    """Normalized matching key from a filename (extension stripped)."""
    return _normalize_text(Path(name).stem)


def parent_components(path: Path, root: Path) -> list[str]:
    """Normalized parent-folder names of path relative to root, deepest first."""
    rel_parent = path.relative_to(root).parent
    return [_normalize_text(p) for p in reversed(rel_parent.parts)]


def find_media_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
    )


def index_sources(source_dir: Path) -> dict[str, list[Path]]:
    """Build {normalized_key: [Path, ...]}, grouping filename collisions.

    Collisions are kept and resolved per encoded file by parent folder.
    """
    index: dict[str, list[Path]] = {}
    for f in find_media_files(source_dir):
        key = normalize_key(f.name)
        if not key:
            continue
        index.setdefault(key, []).append(f)
    return index


# Sentinel returned by resolve_source when a filename collision can't be
# narrowed to a single source by parent-folder comparison.
class Ambiguous:
    def __init__(self, candidates: list[Path]):
        self.candidates = candidates


def resolve_source(
    encoded: Path,
    candidates: list[Path],
    encoded_dir: Path,
    source_dir: Path,
) -> Path | None | Ambiguous:
    """Pick the source for an encoded file from same-named candidates.

    Returns the matched Path, None if no candidate's folder matches, or an
    Ambiguous holding the survivors if a genuine tie can't be broken.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    enc_dirs = parent_components(encoded, encoded_dir)
    cand_dirs = {c: parent_components(c, source_dir) for c in candidates}

    remaining = list(candidates)
    depth = 0
    while len(remaining) > 1:
        if depth >= len(enc_dirs):
            # Encoded file has no deeper folder to disambiguate on.
            return Ambiguous(remaining)
        target = enc_dirs[depth]
        filtered = [
            c for c in remaining
            if depth < len(cand_dirs[c]) and cand_dirs[c][depth] == target
        ]
        if not filtered:
            # No same-named source sits in a matching folder.
            return None
        remaining = filtered
        depth += 1

    return remaining[0]


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Insert audio tracks from source files into matching no-audio encoded files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "encoded_dir",
        type=Path,
        help="Folder of encoded MKV files that are missing audio.",
    )
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Folder of source files with audio (searched recursively).",
    )
    args = parser.parse_args()
    for label, path in (("encoded_dir", args.encoded_dir), ("source_dir", args.source_dir)):
        if not path.is_dir():
            parser.error(f"{label}: not a valid directory: {path}")
    return args


def main():
    check_dependencies()
    args = parse_args()
    encoded_dir = args.encoded_dir
    source_dir = args.source_dir

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Insert audio into no-audio encoded files{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")

    print(f"{CYAN}Indexing source files...{RESET} {DIM}{source_dir}{RESET}")
    sources = index_sources(source_dir)
    total_srcs = sum(len(v) for v in sources.values())
    print(f"  {DIM}{total_srcs} source file(s), {len(sources)} unique key(s){RESET}\n")

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
    ambiguous = 0
    failed = 0

    for enc in encoded_files:
        rel = enc.relative_to(encoded_dir)
        key = normalize_key(enc.name)
        src = resolve_source(enc, sources.get(key, []), encoded_dir, source_dir)

        if src is None:
            print(f"  {YELLOW}[NO MATCH]{RESET} {rel}")
            not_found += 1
            continue

        if isinstance(src, Ambiguous):
            print(f"  {YELLOW}[AMBIGUOUS]{RESET} {rel}  {DIM}← can't disambiguate by folder:{RESET}")
            for c in src.candidates:
                print(f"      {DIM}{c}{RESET}")
            ambiguous += 1
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
    print(f"  {YELLOW}ambiguous:{RESET}     {ambiguous}")
    print(f"  {YELLOW}no audio in src:{RESET} {skipped_no_audio}")
    print(f"  {RED}failed:{RESET}        {failed}")

    if not_found or ambiguous or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
