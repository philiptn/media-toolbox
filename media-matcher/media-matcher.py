#!/usr/bin/env python3
"""
Media File Matcher & Renamer
Matches remuxed media files to finished files by comparing audio fingerprints,
then renames the remuxed files to match the finished ones.
"""

import math
import os
import struct
import subprocess
import sys
import shutil
from pathlib import Path


# ── Config ───────────────────────────────────────────────────────────────────

MEDIA_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m2ts", ".ts", ".mpg", ".mpeg", ".wmv", ".flv", ".webm"}
# Fractions of the file duration at which to sample audio (e.g. 0.25 = 25% through).
SAMPLE_FRACTIONS = [0.25, 0.50, 0.75]
# Seconds of audio to extract per sample point.
AUDIO_CLIP_SECS = 3
# Number of RMS energy windows per clip — forms the fingerprint vector.
AUDIO_WINDOWS = 80
# How many audio streams to try per remuxed file.
# Remuxes often carry DTS/TrueHD as stream 0 while the finished file uses a different track.
MAX_AUDIO_STREAMS = 4
# Maximum duration difference (seconds) for two files to be considered candidates.
DURATION_TOLERANCE_SECS = 120
# Cosine similarity threshold for one sample clip to count as a match (0–1).
SIMILARITY_THRESHOLD = 0.85
# Minimum number of sample clips that must match to accept two files as the same content.
MIN_SAMPLE_MATCHES = 2

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"


# ── Helpers ──────────────────────────────────────────────────────────────────

def clean_path(raw: str) -> str:
    """Handle drag-and-drop paths: strip whitespace, quotes, trailing backslash-spaces."""
    p = raw.strip()
    if (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
        p = p[1:-1]
    p = p.replace("\\ ", " ")
    return p


def get_duration(filepath: str) -> float | None:
    """Get media duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def extract_audio_clip(filepath: str, timestamp: float,
                       stream_idx: int | None = None) -> tuple[list[float] | None, str]:
    """
    Extract AUDIO_CLIP_SECS of audio at `timestamp`, downsample to mono 4 kHz,
    and return (fingerprint, ffmpeg_stderr). fingerprint is None on failure.

    Uses fast container seek + a 2-second pre-roll so the audio decoder (e.g. AC-3)
    has time to sync before we start capturing — avoids all-zero output on long seeks.
    stream_idx=None lets ffmpeg auto-select; an integer maps to 0:a:<n>.
    """
    try:
        pre_roll = 2.0
        seek_to = max(0.0, timestamp - pre_roll)
        skip = timestamp - seek_to   # actual gap to discard after fast seek

        audio_args = ["-map", f"0:a:{stream_idx}"] if stream_idx is not None else ["-vn"]

        cmd = ["ffmpeg", "-y",
               "-ss", str(seek_to), "-i", filepath,   # fast container seek
               "-ss", str(skip),                       # decoder-accurate skip within stream
               "-t", str(AUDIO_CLIP_SECS),
               *audio_args,
               "-ac", "1",     # mono
               "-ar", "4000",  # 4 kHz — sufficient for fingerprinting
               "-f", "s16le",  # raw signed 16-bit LE PCM → stdout
               "-"]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        stderr = result.stderr.decode(errors="replace")
        cmd_str = " ".join(cmd)
        data = result.stdout
        n = len(data) // 2
        if n < AUDIO_WINDOWS:
            return None, f"only {n} samples (need {AUDIO_WINDOWS})\n$ {cmd_str}\n{stderr}"
        samples = struct.unpack(f"<{n}h", data[:n * 2])
        ws = n // AUDIO_WINDOWS
        envelope = [
            math.sqrt(sum(s * s for s in samples[i * ws:(i + 1) * ws]) / ws)
            for i in range(AUDIO_WINDOWS)
        ]
        peak = max(envelope)
        if peak < 1:
            return None, f"audio is silence (peak={peak:.1f})\n$ {cmd_str}\n{stderr}"
        return [v / peak for v in envelope], ""
    except Exception as e:
        return None, str(e)


def audio_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two normalised RMS-envelope vectors."""
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    mag_a = math.sqrt(sum(x * x for x in a[:n]))
    mag_b = math.sqrt(sum(x * x for x in b[:n]))
    if mag_a < 1e-9 or mag_b < 1e-9:
        return 0.0
    return dot / (mag_a * mag_b)


def collect_media_files(folder: str) -> list[Path]:
    """Find all media files in a folder (non-recursive)."""
    return [
        f for f in sorted(Path(folder).iterdir())
        if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS
    ]


def compute_fingerprints(filepath: Path, duration: float, label: str = "",
                         stream_idx: int | None = None) -> list[list[float] | None]:
    """Sample audio at each SAMPLE_FRACTIONS position; return one envelope per fraction."""
    fps: list[list[float] | None] = []
    for frac in SAMPLE_FRACTIONS:
        ts = max(0.0, duration * frac - AUDIO_CLIP_SECS / 2)
        fp, _ = extract_audio_clip(str(filepath), ts, stream_idx)
        fps.append(fp)
    if label:
        ok = sum(1 for fp in fps if fp is not None)
        status = f"{GREEN}OK{RESET} ({ok}/{len(fps)} clips)" if ok else f"{RED}FAIL{RESET}"
        print(f"  {DIM}[{status}{DIM}]{RESET} {label}")
    return fps


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Media File Matcher & Renamer{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    print(f"{DIM}  Matches remuxed files to finished files via audio fingerprint{RESET}")
    print(f"{DIM}  Drag & drop folders into the terminal when prompted{RESET}\n")

    # ── Get paths ────────────────────────────────────────────────────────
    print(f"{CYAN}Finished files folder{RESET} (the correctly named ones):")
    finished_dir = clean_path(input("  > "))
    if not os.path.isdir(finished_dir):
        print(f"{RED}Not a valid directory: {finished_dir}{RESET}")
        sys.exit(1)

    print(f"\n{CYAN}Remuxed files folder{RESET} (the ones to be renamed):")
    remuxed_dir = clean_path(input("  > "))
    if not os.path.isdir(remuxed_dir):
        print(f"{RED}Not a valid directory: {remuxed_dir}{RESET}")
        sys.exit(1)

    if os.path.abspath(finished_dir) == os.path.abspath(remuxed_dir):
        print(f"{RED}Both paths point to the same directory. Exiting.{RESET}")
        sys.exit(1)

    # ── Scan files ───────────────────────────────────────────────────────
    finished_files = collect_media_files(finished_dir)
    remuxed_files = collect_media_files(remuxed_dir)

    print(f"\n{BOLD}Found:{RESET}  {len(finished_files)} finished  |  {len(remuxed_files)} remuxed\n")

    if not finished_files or not remuxed_files:
        print(f"{RED}Need at least one file in each folder.{RESET}")
        sys.exit(1)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print(f"{RED}ffmpeg/ffprobe not found. Install ffmpeg first.{RESET}")
        sys.exit(1)

    # ── Get durations (cheap ffprobe, no audio extraction) ───────────────
    def fmt_dur(d: float | None) -> str:
        if d is None:
            return "?"
        if d >= 3600:
            return f"{int(d // 3600)}h{int((d % 3600) // 60):02d}m"
        return f"{int(d // 60)}m{int(d % 60):02d}s"

    print(f"{BOLD}Reading file durations...{RESET}")
    finished_durations: dict[Path, float | None] = {}
    for f in finished_files:
        d = get_duration(str(f))
        finished_durations[f] = d
        print(f"  {DIM}[{fmt_dur(d):>7}]{RESET} {f.name}")

    print(f"\n  Reading durations for {len(remuxed_files)} remuxed files...", end="", flush=True)
    remuxed_durations: dict[Path, float | None] = {}
    for f in remuxed_files:
        remuxed_durations[f] = get_duration(str(f))
    print(" done")

    # ── Build per-finished-file candidate lists from duration ─────────────
    print(f"\n{BOLD}Filtering candidates by duration...{RESET}")
    candidates_for: dict[Path, list[Path]] = {}
    for fin_path in finished_files:
        fin_dur = finished_durations.get(fin_path)
        cands = sorted(
            (f for f in remuxed_files
             if (fin_dur is None
                 or remuxed_durations.get(f) is None
                 or abs(remuxed_durations[f] - fin_dur) <= DURATION_TOLERANCE_SECS)),
            key=lambda f: abs((remuxed_durations.get(f) or 0) - (fin_dur or 0)),
        )
        candidates_for[fin_path] = cands
        cand_word = "candidate" if len(cands) == 1 else "candidates"
        print(f"  {DIM}{fin_path.name}:{RESET} {CYAN}{len(cands)}{RESET} {cand_word}")

    # ── Fingerprint finished files ────────────────────────────────────────
    print(f"\n{BOLD}Fingerprinting finished files...{RESET}")
    finished_fps: dict[Path, list] = {}
    for f in finished_files:
        dur = finished_durations[f]
        if dur is None:
            print(f"  {RED}SKIP{RESET} {f.name} — duration unknown")
            finished_fps[f] = []
        else:
            finished_fps[f] = compute_fingerprints(f, dur, f.name)

    # ── Verify: fingerprint only duration-filtered candidates ─────────────
    print(f"\n{BOLD}Verifying candidates by audio fingerprint...{RESET}")
    matches = []
    # cache: file → list of per-stream fingerprint lists
    rem_fps_cache: dict[Path, list[list]] = {}

    for fin_path in finished_files:
        fin_fps = finished_fps[fin_path]
        has_audio = any(fp is not None for fp in fin_fps)

        if not has_audio:
            # No audio — fall back to closest duration match
            cands = candidates_for[fin_path]
            if cands:
                matches.append((fin_path, cands[0], 0, 0.0))
                print(f"  {YELLOW}!{RESET} {fin_path.name} — no audio, using closest duration match")
            else:
                print(f"  {RED}SKIP{RESET} {fin_path.name} — no audio and no duration candidates")
            continue

        best_match = None
        best_score = (-1, 0.0)

        for rem_file in candidates_for[fin_path]:
            rem_dur = remuxed_durations.get(rem_file)
            if rem_dur is None:
                continue

            if rem_file not in rem_fps_cache:
                # Fingerprint each audio stream until one yields nothing
                all_streams: list[list] = []
                for si in range(MAX_AUDIO_STREAMS):
                    fps = compute_fingerprints(rem_file, rem_dur, stream_idx=si)
                    if not any(fp is not None for fp in fps):
                        break
                    all_streams.append(fps)
                n_streams = len(all_streams)
                best_clips = max((sum(1 for fp in s if fp is not None)
                                  for s in all_streams), default=0)
                stream_word = "stream" if n_streams == 1 else "streams"
                status = (f"{GREEN}OK{RESET} ({best_clips}/{len(SAMPLE_FRACTIONS)} clips"
                          f", {n_streams} {stream_word})" if n_streams else f"{RED}FAIL{RESET}")
                print(f"  {DIM}[{status}{DIM}]{RESET} {rem_file.name}")
                rem_fps_cache[rem_file] = all_streams

            # Find best similarity across all streams
            for rem_fps in rem_fps_cache[rem_file]:
                matched = 0
                total_sim = 0.0
                for fin_fp, rem_fp in zip(fin_fps, rem_fps):
                    if fin_fp is None or rem_fp is None:
                        continue
                    sim = audio_similarity(fin_fp, rem_fp)
                    if sim >= SIMILARITY_THRESHOLD:
                        matched += 1
                        total_sim += sim

                if matched >= MIN_SAMPLE_MATCHES:
                    avg_sim = total_sim / matched
                    score = (matched, avg_sim)
                    if score > best_score:
                        best_score = score
                        best_match = (fin_path, rem_file, matched, avg_sim)

        if best_match:
            matches.append(best_match)

    if not matches:
        print(f"\n{RED}No matches found.{RESET} Possible causes:")
        print(f"  - Files are different content")
        print(f"  - Audio tracks are missing or completely different between versions")
        print(f"  - Duration filter excluded true matches (try raising DURATION_TOLERANCE_SECS)")
        sys.exit(1)

    # ── Show results ─────────────────────────────────────────────────────
    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}  Proposed renames ({len(matches)} matches){RESET}")
    print(f"{BOLD}{'─' * 60}{RESET}\n")

    renames = []
    for fin_path, rem_path, matched_n, avg_sim in matches:
        new_name = fin_path.stem + rem_path.suffix
        new_path = rem_path.parent / new_name

        conflict = new_path.exists() and new_path != rem_path
        tag = f" {RED}[CONFLICT]{RESET}" if conflict else ""

        print(f"  {rem_path.name}")
        if matched_n == 0:
            print(f"  {YELLOW}→ {new_name}{RESET}  {DIM}(duration match only — verify manually){RESET}{tag}")
        else:
            print(f"  {GREEN}→ {new_name}{RESET}  {DIM}({matched_n}/{len(SAMPLE_FRACTIONS)} clips matched, avg similarity {avg_sim:.1%}){RESET}{tag}")
        print()

        if not conflict:
            renames.append((rem_path, new_path))
        else:
            print(f"    {YELLOW}Skipping: target filename already exists{RESET}")

    # Warn about unmatched
    matched_remuxed = {m[1] for m in matches}
    unmatched_count = sum(1 for f in remuxed_files if f not in matched_remuxed)
    if unmatched_count:
        print(f"{DIM}{unmatched_count} remuxed files had no match.{RESET}\n")

    matched_finished = {m[0] for m in matches}
    unmatched_fin = [f for f in finished_files if f not in matched_finished]
    if unmatched_fin:
        print(f"{YELLOW}Unmatched finished files ({len(unmatched_fin)}):{RESET}")
        for f in unmatched_fin:
            print(f"  {DIM}• {f.name}{RESET}")
        print()

    if not renames:
        print(f"{RED}No renames to perform.{RESET}")
        sys.exit(1)

    # ── Confirm ──────────────────────────────────────────────────────────
    rename_word = "rename" if len(renames) == 1 else "renames"
    print(f"{BOLD}Proceed with {len(renames)} {rename_word}? [y/N]{RESET} ", end="")
    confirm = input().strip().lower()

    if confirm not in ("y", "yes"):
        print(f"{YELLOW}Aborted.{RESET}")
        sys.exit(0)

    # ── Rename (two-pass to avoid A→B, B→A collisions) ───────────────────
    temp_map = []
    for rem_path, new_path in renames:
        if rem_path == new_path:
            continue
        temp_path = rem_path.parent / (rem_path.stem + ".tmp_rename" + rem_path.suffix)
        os.rename(rem_path, temp_path)
        temp_map.append((temp_path, new_path))

    for temp_path, new_path in temp_map:
        os.rename(temp_path, new_path)
        print(f"  {GREEN}✓{RESET} {new_path.name}")

    renamed_word = "file" if len(temp_map) == 1 else "files"
    print(f"\n{GREEN}{BOLD}Done. {len(temp_map)} {renamed_word} renamed.{RESET}\n")


if __name__ == "__main__":
    main()
