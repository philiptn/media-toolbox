#!/usr/bin/env python3
"""
Media File Matcher & Renamer
Matches remuxed media files to finished files by comparing audio and visual
fingerprints, then renames the remuxed files to match the finished ones.
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
# Fractions of the file duration at which to sample (shared by audio and video).
SAMPLE_FRACTIONS = [0.15, 0.30, 0.50, 0.70, 0.85]
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
# dHash grid dimensions: WIDTH columns x HEIGHT rows → (WIDTH-1)*HEIGHT = 64 bits.
DHASH_WIDTH = 9
DHASH_HEIGHT = 8
# Center-crop fraction applied before hashing to strip letterboxing (0.8 = keep central 80%).
FRAME_CROP_FRACTION = 0.8
# Maximum Hamming distance (out of 64 bits) for a frame pair to count as a visual match.
FRAME_MATCH_THRESHOLD = 10
# Minimum combined score (audio points + video points) to accept a match.
# Each sample point can contribute up to 2 points (1 audio + 1 video). Max = 2 * len(SAMPLE_FRACTIONS).
MIN_COMBINED_SCORE = 4

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


def extract_video_frame_hash(filepath: str, timestamp: float) -> int | None:
    """
    Extract a single video frame at `timestamp`, downscale to a tiny grayscale grid,
    and return a 64-bit dHash (difference hash). Returns None on failure.

    The frame is center-cropped by FRAME_CROP_FRACTION to strip letterboxing, then
    scaled to DHASH_WIDTH x DHASH_HEIGHT. Adjacent pixels are compared left-to-right
    to produce a binary hash that is robust to re-encoding, resolution, and color changes.
    """
    try:
        crop = FRAME_CROP_FRACTION
        vf = f"crop=iw*{crop}:ih*{crop},scale={DHASH_WIDTH}:{DHASH_HEIGHT}"
        cmd = ["ffmpeg", "-y",
               "-ss", str(timestamp), "-i", filepath,
               "-vframes", "1",
               "-vf", vf,
               "-pix_fmt", "gray",
               "-f", "rawvideo",
               "-"]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        data = result.stdout
        expected = DHASH_WIDTH * DHASH_HEIGHT
        if len(data) < expected:
            return None
        bits = 0
        for row in range(DHASH_HEIGHT):
            for col in range(DHASH_WIDTH - 1):
                idx = row * DHASH_WIDTH + col
                if data[idx] > data[idx + 1]:
                    bits |= 1 << (row * (DHASH_WIDTH - 1) + col)
        return bits
    except Exception:
        return None


def hamming_distance(a: int, b: int) -> int:
    """Count differing bits between two integers."""
    return bin(a ^ b).count('1')


def compute_video_hashes(filepath: Path, duration: float,
                         label: str = "") -> list[int | None]:
    """Extract a dHash at each SAMPLE_FRACTIONS position; return one hash per fraction."""
    hashes: list[int | None] = []
    for frac in SAMPLE_FRACTIONS:
        ts = duration * frac
        h = extract_video_frame_hash(str(filepath), ts)
        hashes.append(h)
    if label:
        ok = sum(1 for h in hashes if h is not None)
        status = f"{GREEN}OK{RESET} ({ok}/{len(hashes)} frames)" if ok else f"{RED}FAIL{RESET}"
        print(f"  {DIM}[{status}{DIM}]{RESET} {label}")
    return hashes


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
    print(f"{DIM}  Matches remuxed files to finished files via audio + visual fingerprint{RESET}")
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

    # ── Scan finished files ────────────────────────────────────────────────
    finished_files = collect_media_files(finished_dir)

    if not finished_files:
        print(f"{RED}No media files found in finished folder.{RESET}")
        sys.exit(1)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print(f"{RED}ffmpeg/ffprobe not found. Install ffmpeg first.{RESET}")
        sys.exit(1)

    # ── Get finished durations ──────────────────────────────────────────
    def fmt_dur(d: float | None) -> str:
        if d is None:
            return "?"
        if d >= 3600:
            return f"{int(d // 3600)}h{int((d % 3600) // 60):02d}m"
        return f"{int(d // 60)}m{int(d % 60):02d}s"

    print(f"\n{BOLD}Found {len(finished_files)} finished files. Reading durations...{RESET}")
    finished_durations: dict[Path, float | None] = {}
    for f in finished_files:
        d = get_duration(str(f))
        finished_durations[f] = d
        print(f"  {DIM}[{fmt_dur(d):>7}]{RESET} {f.name}")

    # ── Fingerprint finished files (audio + video) ─────────────────────────
    print(f"\n{BOLD}Fingerprinting finished files (audio)...{RESET}")
    finished_audio: dict[Path, list] = {}
    for f in finished_files:
        dur = finished_durations[f]
        if dur is None:
            print(f"  {RED}SKIP{RESET} {f.name} — duration unknown")
            finished_audio[f] = []
        else:
            finished_audio[f] = compute_fingerprints(f, dur, f.name)

    print(f"\n{BOLD}Fingerprinting finished files (video)...{RESET}")
    finished_video: dict[Path, list[int | None]] = {}
    for f in finished_files:
        dur = finished_durations[f]
        if dur is None:
            finished_video[f] = []
        else:
            finished_video[f] = compute_video_hashes(f, dur, f.name)

    # ── Process remuxed folders (loop) ──────────────────────────────────
    first_remuxed_run = True
    while True:
        remuxed_files = collect_media_files(remuxed_dir)

        if first_remuxed_run:
            print(f"\n{BOLD}Found:{RESET}  {len(finished_files)} finished  |  {len(remuxed_files)} remuxed\n")
        else:
            print(f"\n{BOLD}Found:{RESET}  {len(remuxed_files)} remuxed files\n")

        if not remuxed_files:
            print(f"{RED}No media files found in remuxed folder.{RESET}")
        else:
            print(f"  Reading durations for {len(remuxed_files)} remuxed files...", end="", flush=True)
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

            # ── Verify: fingerprint candidates and score with combined matching ───
            print(f"\n{BOLD}Verifying candidates by audio + visual fingerprint...{RESET}")
            matches: list[tuple[Path, Path, int, int, int, float]] = []
            rem_cache: dict[Path, tuple[list[list], list[int | None]]] = {}

            for fin_path in finished_files:
                fin_afps = finished_audio[fin_path]
                fin_vhashes = finished_video.get(fin_path, [])
                has_audio = any(fp is not None for fp in fin_afps)
                has_video = any(h is not None for h in fin_vhashes)

                if not has_audio and not has_video:
                    cands = candidates_for[fin_path]
                    if cands:
                        matches.append((fin_path, cands[0], 0, 0, 0, 0.0))
                        print(f"  {YELLOW}!{RESET} {fin_path.name} — no fingerprints, using closest duration match")
                    else:
                        print(f"  {RED}SKIP{RESET} {fin_path.name} — no fingerprints and no duration candidates")
                    continue

                best_match = None
                best_score = (-1, 0.0)

                for rem_file in candidates_for[fin_path]:
                    rem_dur = remuxed_durations.get(rem_file)
                    if rem_dur is None:
                        continue

                    if rem_file not in rem_cache:
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
                        a_status = (f"{best_clips}/{len(SAMPLE_FRACTIONS)} clips"
                                    f", {n_streams} {stream_word}" if n_streams else "no audio")

                        vhashes = compute_video_hashes(rem_file, rem_dur)
                        v_ok = sum(1 for h in vhashes if h is not None)
                        v_status = f"{v_ok}/{len(SAMPLE_FRACTIONS)} frames"

                        status = f"{GREEN}OK{RESET} ({a_status} | {v_status})" if (n_streams or v_ok) else f"{RED}FAIL{RESET}"
                        print(f"  {DIM}[{status}{DIM}]{RESET} {rem_file.name}")
                        rem_cache[rem_file] = (all_streams, vhashes)

                    rem_audio_streams, rem_vhashes = rem_cache[rem_file]

                    audio_stream_list = rem_audio_streams if rem_audio_streams else [[None] * len(SAMPLE_FRACTIONS)]
                    for rem_afps in audio_stream_list:
                        combined = 0
                        audio_pts = 0
                        video_pts = 0
                        audio_sim_total = 0.0
                        audio_match_count = 0

                        for i in range(len(SAMPLE_FRACTIONS)):
                            fin_afp = fin_afps[i] if i < len(fin_afps) else None
                            rem_afp = rem_afps[i] if i < len(rem_afps) else None
                            if fin_afp is not None and rem_afp is not None:
                                sim = audio_similarity(fin_afp, rem_afp)
                                if sim >= SIMILARITY_THRESHOLD:
                                    combined += 1
                                    audio_pts += 1
                                    audio_sim_total += sim
                                    audio_match_count += 1

                            fin_vh = fin_vhashes[i] if i < len(fin_vhashes) else None
                            rem_vh = rem_vhashes[i] if i < len(rem_vhashes) else None
                            if fin_vh is not None and rem_vh is not None:
                                if hamming_distance(fin_vh, rem_vh) <= FRAME_MATCH_THRESHOLD:
                                    combined += 1
                                    video_pts += 1

                        if combined >= MIN_COMBINED_SCORE:
                            avg_sim = audio_sim_total / audio_match_count if audio_match_count else 0.0
                            score = (combined, avg_sim)
                            if score > best_score:
                                best_score = score
                                best_match = (fin_path, rem_file, combined, audio_pts, video_pts, avg_sim)

                if best_match:
                    matches.append(best_match)

            # Deduplicate: if the same remuxed file matched multiple finished files, keep best
            seen_remuxed: dict[Path, tuple] = {}
            for match in matches:
                _, rem_path, combined_score, _, _, avg_sim = match
                prev = seen_remuxed.get(rem_path)
                if prev is None or (combined_score, avg_sim) > (prev[2], prev[5]):
                    seen_remuxed[rem_path] = match
            matches = list(seen_remuxed.values())

            if not matches:
                print(f"\n{RED}No matches found.{RESET} Possible causes:")
                print(f"  - Files are different content")
                print(f"  - Audio/video tracks missing or completely different between versions")
                print(f"  - Duration filter excluded true matches (try raising DURATION_TOLERANCE_SECS)")
            else:
                # ── Show results ─────────────────────────────────────────────────────
                print(f"\n{BOLD}{'─' * 60}{RESET}")
                print(f"{BOLD}  Proposed renames ({len(matches)} matches){RESET}")
                print(f"{BOLD}{'─' * 60}{RESET}\n")

                max_score = 2 * len(SAMPLE_FRACTIONS)
                renames = []
                for fin_path, rem_path, combined_score, audio_pts, video_pts, avg_sim in matches:
                    new_name = fin_path.stem + rem_path.suffix
                    new_path = rem_path.parent / new_name

                    conflict = new_path.exists() and new_path != rem_path
                    tag = f" {RED}[CONFLICT]{RESET}" if conflict else ""

                    print(f"  {rem_path.name}")
                    if combined_score == 0:
                        print(f"  {YELLOW}→ {new_name}{RESET}  {DIM}(duration match only — verify manually){RESET}{tag}")
                    else:
                        sim_str = f", avg sim {avg_sim:.1%}" if audio_pts else ""
                        print(f"  {GREEN}→ {new_name}{RESET}  {DIM}(score {combined_score}/{max_score}: {audio_pts}a+{video_pts}v{sim_str}){RESET}{tag}")
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

                if renames:
                    # ── Confirm ──────────────────────────────────────────────────────────
                    rename_word = "rename" if len(renames) == 1 else "renames"
                    print(f"{BOLD}Proceed with {len(renames)} {rename_word}? [y/N]{RESET} ", end="")
                    confirm = input().strip().lower()

                    if confirm in ("y", "yes"):
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
                    else:
                        print(f"{YELLOW}Aborted.{RESET}")
                else:
                    print(f"{RED}No renames to perform.{RESET}")

        # ── Ask to process another remuxed folder ───────────────────────────
        first_remuxed_run = False
        print(f"{BOLD}Rename more remuxed files using the same finished hashes? [y/N]{RESET} ", end="")
        again = input().strip().lower()
        if again not in ("y", "yes"):
            break

        print(f"\n{CYAN}Remuxed files folder{RESET} (the ones to be renamed):")
        remuxed_dir = clean_path(input("  > "))
        if not os.path.isdir(remuxed_dir):
            print(f"{RED}Not a valid directory: {remuxed_dir}{RESET}")
            continue

        if os.path.abspath(finished_dir) == os.path.abspath(remuxed_dir):
            print(f"{RED}Both paths point to the same directory.{RESET}")
            continue


if __name__ == "__main__":
    main()
