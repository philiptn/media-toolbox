#!/usr/bin/env python3
"""
Convert SRT subtitle tracks in MKV files to styled ASS subtitles.

Reads all MKV files in a given folder, extracts SRT tracks, converts them
to ASS with custom styling, attaches the specified font, and remuxes
everything back into the MKV — overwriting the original.

Requires: mkvtoolnix (mkvmerge, mkvextract) and python3.
Install on Fedora/Nobara: sudo dnf install mkvtoolnix

Style based on:
  --sub-font="DejaVu Sans"
  --sub-bold=yes
  --sub-color=1.0/1.0        (white, full opacity)
  --sub-border-size=2
  --sub-shadow-offset=1.5
  --sub-shadow-color=0/0/0/1.0 (black, full opacity)
  --sub-spacing=0.5
  --sub-font-size=40
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def check_dependencies():
    for tool in ("mkvmerge", "mkvextract"):
        if not shutil.which(tool):
            print(f"Error: '{tool}' not found. Install mkvtoolnix:")
            print("  sudo dnf install mkvtoolnix")
            sys.exit(1)


def get_tracks(mkv_path):
    """Return track info via mkvmerge --identify --identification-format json."""
    result = subprocess.run(
        ["mkvmerge", "-J", mkv_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  Warning: mkvmerge failed to identify {mkv_path}")
        return None
    return json.loads(result.stdout)


def extract_srt_tracks(mkv_path, info, tmp_dir):
    """Extract all SRT subtitle tracks. Returns list of (track_id, language, track_name, srt_path)."""
    srt_tracks = []
    for track in info.get("tracks", []):
        if track["type"] != "subtitles":
            continue
        codec = track["properties"].get("codec_id", "")
        # S_TEXT/UTF8 is SRT in Matroska
        if codec != "S_TEXT/UTF8":
            continue

        tid = track["id"]
        lang = track["properties"].get("language", "und")
        tname = track["properties"].get("track_name", "")
        srt_path = os.path.join(tmp_dir, f"track_{tid}.srt")
        srt_tracks.append((tid, lang, tname, srt_path))

    if not srt_tracks:
        return []

    # Build mkvextract command
    extract_args = ["mkvextract", "tracks", mkv_path]
    for tid, _, _, srt_path in srt_tracks:
        extract_args.append(f"{tid}:{srt_path}")

    result = subprocess.run(extract_args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Warning: mkvextract failed: {result.stderr.strip()}")
        return []

    return srt_tracks


def parse_srt(srt_path):
    """Parse SRT file into list of (index, start, end, text) tuples."""
    with open(srt_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Normalize line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    blocks = re.split(r"\n\n+", content.strip())
    events = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # Find the timestamp line
        ts_line = None
        ts_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                ts_line = line
                ts_idx = i
                break

        if ts_line is None:
            continue

        match = re.match(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            ts_line.strip()
        )
        if not match:
            continue

        h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
        start = f"{int(h1)}:{m1}:{s1}.{ms1[:2]}"
        end = f"{int(h2)}:{m2}:{s2}.{ms2[:2]}"

        text_lines = lines[ts_idx + 1:]
        text = "\\N".join(text_lines)

        # Strip basic HTML-like tags from SRT
        text = re.sub(r"<[^>]+>", "", text)

        events.append((start, end, text))

    return events


DEFAULT_FONT_SIZE = 45

# Plex extras suffixes and folder names — these get internal muxing even with --external
PLEX_EXTRAS_SUFFIXES = (
    "-behindthescenes", "-deleted", "-featurette", "-interview",
    "-scene", "-short", "-trailer", "-other",
)
PLEX_EXTRAS_FOLDERS = {
    "behind the scenes", "deleted scenes", "featurettes", "interviews",
    "scenes", "shorts", "trailers", "other",
}


def is_plex_extra(mkv_path):
    """Check if a file is a Plex extra by suffix or parent folder name."""
    stem = os.path.splitext(os.path.basename(mkv_path))[0].lower()
    for suffix in PLEX_EXTRAS_SUFFIXES:
        if stem.endswith(suffix):
            return True
    parent = os.path.basename(os.path.dirname(mkv_path)).lower()
    return parent in PLEX_EXTRAS_FOLDERS


def srt_to_ass(srt_path, ass_path, font_size=DEFAULT_FONT_SIZE):
    """Convert an SRT file to a styled ASS file."""
    events = parse_srt(srt_path)

    ass_header = f"""\ufeff[Script Info]
Title: Converted from SRT
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0.5,0,1,2,1.5,2,20,20,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        for start, end, text in events:
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")


def find_font_path(font_name):
    """Try to find the font file on the system using fc-match."""
    try:
        result = subprocess.run(
            ["fc-match", "--format=%{file}", font_name],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            if os.path.isfile(path):
                return path
    except Exception:
        pass
    return None


def remux_mkv(mkv_path, info, srt_tracks, ass_files, font_path, tmp_dir):
    """
    Remux the MKV:
    - Keep all existing tracks as-is (including SRT)
    - Add new ASS tracks (not set as default)
    - Attach the font file
    - Overwrite the original
    """
    out_path = os.path.join(tmp_dir, "output.mkv")

    cmd = ["mkvmerge", "-o", out_path]

    # Find existing ASS tracks to exclude (they'll be replaced by new ones)
    ass_tids = []
    for track in info.get("tracks", []):
        if track["type"] == "subtitles" and track["properties"].get("codec_id") == "S_TEXT/ASS":
            ass_tids.append(track["id"])

    if ass_tids:
        exclude = ",".join(str(t) for t in ass_tids)
        cmd += ["--subtitle-tracks", f"!{exclude}"]
        print(f"  Replacing {len(ass_tids)} existing ASS track(s)")

    cmd.append(mkv_path)

    # Add each ASS file with matching language and track name, not as default
    for i, (_, lang, tname, _) in enumerate(srt_tracks):
        ass_path = ass_files[i]
        cmd += ["--default-track-flag", "0:no"]
        cmd += ["--language", f"0:{lang}"]
        if tname:
            cmd += ["--track-name", f"0:{tname}"]
        cmd.append(ass_path)

    # Attach the font if found
    if font_path:
        mime = "font/ttf"
        if font_path.endswith(".otf"):
            mime = "font/otf"
        cmd += [
            "--attachment-mime-type", mime,
            "--attachment-name", os.path.basename(font_path),
            "--attach-file", font_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Error remuxing: {result.stderr.strip()}")
        return False

    # Overwrite original
    shutil.move(out_path, mkv_path)
    return True


ISO_639_2_TO_1 = {
    "eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
    "spa": "es", "ita": "it", "por": "pt", "rus": "ru", "jpn": "ja",
    "kor": "ko", "chi": "zh", "zho": "zh", "ara": "ar", "hin": "hi",
    "tur": "tr", "pol": "pl", "nld": "nl", "dut": "nl", "swe": "sv",
    "nor": "no", "nob": "nb", "nno": "nn", "dan": "da", "fin": "fi",
    "ces": "cs", "cze": "cs", "hun": "hu", "ron": "ro", "rum": "ro",
    "ell": "el", "gre": "el", "heb": "he", "tha": "th", "vie": "vi",
    "ind": "id", "msa": "ms", "may": "ms", "ukr": "uk", "bul": "bg",
    "hrv": "hr", "srp": "sr", "slk": "sk", "slo": "sk", "slv": "sl",
    "cat": "ca", "eus": "eu", "baq": "eu", "glg": "gl", "lit": "lt",
    "lav": "lv", "est": "et", "ice": "is", "isl": "is",
}


def write_external_ass(mkv_path, srt_tracks, ass_files):
    """Place .ass files beside the MKV file, named for Plex/Jellyfin pickup."""
    base = os.path.splitext(mkv_path)[0]
    for i, (_, lang, _, _) in enumerate(srt_tracks):
        lang_short = ISO_639_2_TO_1.get(lang, lang) if lang and lang != "und" else ""
        suffix = f".{lang_short}" if lang_short else ""
        out_path = f"{base}{suffix}.ass"
        shutil.copy2(ass_files[i], out_path)
        print(f"  Written external: {os.path.basename(out_path)}")


def process_file(mkv_path, font_path, font_size=DEFAULT_FONT_SIZE, external=False):
    """Process a single MKV file."""
    print(f"Processing: {os.path.basename(mkv_path)}")

    info = get_tracks(mkv_path)
    if info is None:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        srt_tracks = extract_srt_tracks(mkv_path, info, tmp_dir)

        if not srt_tracks:
            print("  No SRT tracks found, skipping.")
            return

        print(f"  Found {len(srt_tracks)} SRT track(s)")

        # Convert each SRT to ASS
        ass_files = []
        for tid, lang, tname, srt_path in srt_tracks:
            ass_path = os.path.join(tmp_dir, f"track_{tid}.ass")
            srt_to_ass(srt_path, ass_path, font_size)
            ass_files.append(ass_path)
            label = f"{lang}" + (f" ({tname})" if tname else "")
            print(f"  Converted track {tid} [{label}] to ASS")

        # External mode: place .ass beside the file (unless it's a Plex extra)
        if external and not is_plex_extra(mkv_path):
            write_external_ass(mkv_path, srt_tracks, ass_files)
            print("  Done — external ASS files written.")
        else:
            if external:
                print("  Plex extra detected — muxing internally instead.")
            if remux_mkv(mkv_path, info, srt_tracks, ass_files, font_path, tmp_dir):
                print("  Done — ASS tracks added.")
            else:
                print("  Failed — original file unchanged.")


def main():
    parser = argparse.ArgumentParser(
        description="Convert SRT tracks in MKV files to styled ASS subtitles."
    )
    parser.add_argument(
        "folder",
        help="Path to folder containing MKV files"
    )
    parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Process subfolders recursively"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done without modifying files"
    )
    parser.add_argument(
        "--external", "-e",
        action="store_true",
        help="Place .ass files beside the MKV instead of muxing (extras are always muxed internally)"
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=DEFAULT_FONT_SIZE,
        help=f"ASS subtitle font size (default: {DEFAULT_FONT_SIZE})"
    )
    args = parser.parse_args()

    folder = os.path.abspath(args.folder)
    if not os.path.isdir(folder):
        print(f"Error: '{folder}' is not a directory.")
        sys.exit(1)

    check_dependencies()

    # Find font
    font_path = find_font_path("DejaVu Sans")
    if font_path:
        print(f"Font found: {font_path}")
    else:
        print("Warning: DejaVu Sans font file not found — will not embed font.")
        print("  Subtitle will still reference it but playback depends on device having it.")

    # Collect MKV files
    mkv_files = []
    if args.recursive:
        for root, dirs, files in os.walk(folder):
            for f in sorted(files):
                if f.lower().endswith(".mkv"):
                    mkv_files.append(os.path.join(root, f))
    else:
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".mkv"):
                mkv_files.append(os.path.join(folder, f))

    if not mkv_files:
        print("No MKV files found.")
        sys.exit(0)

    print(f"\nFound {len(mkv_files)} MKV file(s)\n")

    if args.dry_run:
        for mkv in mkv_files:
            print(f"  Would process: {os.path.basename(mkv)}")
        print("\nDry run — no files modified.")
        return

    for mkv in mkv_files:
        process_file(mkv, font_path, args.font_size, args.external)
        print()

    print("All done.")


if __name__ == "__main__":
    main()