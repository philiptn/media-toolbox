import argparse
import os
import glob
import sys
import time
from pymediainfo import MediaInfo
from fractions import Fraction
import threading
import subprocess
import json
import numpy as np
import multiprocessing as mp
from tqdm import tqdm
import signal
import platform

signal.signal(signal.SIGINT, signal.SIG_DFL)


ANALYZE_FRAMES = 120
SEGMENT_POSITIONS = [0.1, 0.5, 0.9]

# Frames whose inter-frame motion is below this (mean abs gray delta, 0–255 scale)
# carry no usable interlace signal — their field/frame ratio is dominated by noise.
MOTION_THRESHOLD = 1.5

# Pooled median field/frame ratio: ~0.5 for true 60i, ~1.0 for progressive.
# Only double the framerate when the ratio sits firmly in the interlaced zone.
INTERLACED_RATIO_MAX = 0.65

cpu_total = os.cpu_count() or 4
workers = max(1, int(cpu_total * 0.4))


def get_video_info_ffprobe(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration:format=duration",
        "-of", "json", path
    ]
    data = json.loads(subprocess.check_output(cmd))
    s = data["streams"][0]

    w = int(s["width"])
    h = int(s["height"])
    dur = float(s.get("duration", 0) or 0)
    if dur <= 0:
        # Stream duration is often N/A for MKV; fall back to container duration.
        dur = float(data.get("format", {}).get("duration", 0) or 0)
    if dur <= 0:
        dur = 60

    return w, h, dur


def segment_ratios(path, w, h, start, frames=ANALYZE_FRAMES):
    """
    Read `frames` grayscale frames starting at `start` and return per-frame
    (field/frame ratio, frame motion) pairs. The ratio compares
    top@N vs bottom@N-1 (a 1/60s gap in true 60i, a full 1/30s gap in 30p
    stored as 60i) against the full frame@N vs frame@N-1 motion. True 60i
    drives the ratio toward 0.5; progressive content stored interlaced
    drives it toward 1.0.
    """
    cmd = [
        "ffmpeg",
        "-ss", str(start),
        "-i", path,
        "-an",
        "-vf", "format=gray",
        "-frames:v", str(frames),
        "-f", "rawvideo",
        "-"
    ]

    pipe = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h

    prev_frame = None
    prev_bottom = None
    out = []

    for _ in range(frames):
        raw = pipe.stdout.read(size)
        if len(raw) < size:
            break

        f = np.frombuffer(raw, dtype=np.uint8).reshape((h, w))
        bottom = f[1::2]

        if prev_frame is not None and prev_bottom is not None:
            top = f[0::2]
            frame_diff = float(np.mean(np.abs(f.astype(np.int16) - prev_frame.astype(np.int16))))
            field_diff = float(np.mean(np.abs(top.astype(np.int16) - prev_bottom.astype(np.int16))))
            if frame_diff > 0:
                out.append((field_diff / frame_diff, frame_diff))

        prev_frame = f
        prev_bottom = bottom

    pipe.terminate()
    return out


def detect_motion_type(path):
    try:
        w, h, dur = get_video_info_ffprobe(path)
    except Exception:
        return "analysis_failed"

    pooled = []
    for pos in SEGMENT_POSITIONS:
        for ratio, motion in segment_ratios(path, w, h, dur * pos):
            if motion >= MOTION_THRESHOLD:
                pooled.append(ratio)

    # No moving frames → can't tell interlaced from progressive. The safe
    # call (and the user's stated preference) is to leave the FPS alone.
    if not pooled:
        return "low_motion"

    if float(np.median(pooled)) < INTERLACED_RATIO_MAX:
        return "true_60i"
    return "progressive"


def process_video(video_file):
    media_info = MediaInfo.parse(video_file)

    codec = codec_profile = fps = 'Unknown'
    field_order = aspect_ratio = resolution = 'Unknown'
    avg_bitrate = max_bitrate = None
    duration_seconds = None
    duration_display = 'Unknown'

    # ---- Video track ----
    for track in media_info.tracks:
        if track.track_type == 'Video':
            codec = track.codec_id or 'Unknown'
            codec_profile = track.format_profile or 'Unknown'
            fps = track.frame_rate or 'Unknown'

            if track.scan_type in ('Interlaced', 'MBAFF'):
                field_order = track.scan_order or 'Interlaced'
            else:
                field_order = 'Progressive'

            if track.display_aspect_ratio:
                try:
                    frac = Fraction(float(track.display_aspect_ratio)).limit_denominator(100)
                    aspect_ratio = f"{frac.numerator}:{frac.denominator}"
                except ValueError:
                    aspect_ratio = track.display_aspect_ratio
            elif track.width and track.height:
                frac = Fraction(track.width, track.height).limit_denominator(100)
                aspect_ratio = f"{frac.numerator}:{frac.denominator}"

            resolution = f"{track.width}x{track.height}" if track.width and track.height else 'Unknown'

            if track.bit_rate:
                avg_bitrate = int(track.bit_rate) / 1_000_000
            if track.maximum_bit_rate:
                max_bitrate = int(track.maximum_bit_rate) / 1_000_000

            if track.duration:
                try:
                    duration_ms = float(track.duration)
                    duration_seconds = int(duration_ms // 1000)
                    h = duration_seconds // 3600
                    m = (duration_seconds % 3600) // 60
                    s = duration_seconds % 60
                    duration_display = f"{h}:{m:02d}:{s:02d}"
                except (ValueError, TypeError):
                    duration_seconds = None
                    duration_display = 'Unknown'
            break

    # ---- Motion / Deinterlace ----
    deint_fps_value = None

    try:
        fps_value = float(fps)
    except:
        fps_value = None

    if fps_value:
        if field_order != 'Progressive':
            motion_type = detect_motion_type(video_file)
            if motion_type == "true_60i":
                deint_fps_value = fps_value * 2
            else:
                deint_fps_value = fps_value
        else:
            deint_fps_value = fps_value
    effective_fps = deint_fps_value if deint_fps_value is not None else fps_value
    fps_display_value = str(fps)

    if fps_value is not None and field_order != 'Progressive' and deint_fps_value is not None:
        if deint_fps_value != fps_value:
            orig = f"{fps_value:.3f}".rstrip('0').rstrip('.')
            deint = f"{deint_fps_value:.3f}".rstrip('0').rstrip('.')
            fps_display_value = f"{orig}➔{deint}"

    # ---- Audio ----
    audio_tracks = [t for t in media_info.tracks if t.track_type == 'Audio']
    audio_items = []
    for t in audio_tracks:
        lang = (t.language or 'und').lower()
        fmt = (t.format or '').upper()
        default = '*' if getattr(t, 'default', None) == 'Yes' else ''
        audio_items.append(f"{lang}-{fmt}{default}")

    audio_lang = ', '.join(audio_items)

    # ---- Subtitles ----
    subtitle_tracks = [t for t in media_info.tracks if t.track_type == 'Text']
    subtitle_items = []
    for t in subtitle_tracks:
        lang = (t.language or 'und').lower()
        fmt = (t.format or '').upper()
        default = '*' if getattr(t, 'default', None) == 'Yes' else ''
        subtitle_items.append(f"{lang}-{fmt}{default}")

    subtitle_lang = ', '.join(subtitle_items)

    # ---- Filesize ----
    filesize_bytes = os.path.getsize(video_file)
    if filesize_bytes >= 1024 ** 3:
        filesize_display = f"{filesize_bytes / (1024 ** 3):.2f} GB"
    else:
        filesize_display = f"{filesize_bytes / (1024 ** 2):.2f} MB"

    return {
        'filename': os.path.basename(video_file),
        'filesize': filesize_bytes,
        'filesize_display': filesize_display,
        'duration': duration_seconds,
        'duration_display': duration_display,
        'codec': codec,
        'codec_profile': codec_profile,
        'fps': effective_fps,
        'fps_display': fps_display_value,
        'interlace': field_order,
        'aspect': aspect_ratio,
        'resolution': resolution,
        'avg_bitrate': avg_bitrate,
        'avg_bitrate_display': f"{avg_bitrate:.2f} Mbps" if avg_bitrate else 'Unknown',
        'max_bitrate': max_bitrate,
        'max_bitrate_display': f"{max_bitrate:.2f} Mbps" if max_bitrate else 'N/A',
        'audio_lang': audio_lang,
        'subtitle_lang': subtitle_lang
    }


def main():
    parser = argparse.ArgumentParser(description='Check video files in a folder and display metadata.')
    parser.add_argument('folder', nargs='?', default='.', help='Path to the folder containing video files.')
    parser.add_argument('-r', '--recursive', action='store_true', help='Recursively search subfolders.')
    parser.add_argument('--simple', action='store_true',
                        help='Show only filename, filesize, and duration.')
    parser.add_argument('--sort', choices=[
        'filesize', 'codec', 'codec_profile', 'fps', 'interlace', 'aspect',
        'resolution', 'avg_bitrate', 'max_bitrate', 'filename',
        'audio', 'subtitles', 'duration'
    ], help='Column to sort by. Defaults to filename.')
    parser.add_argument('--exclude',
                        help='Comma-separated list of fields to exclude.')

    args = parser.parse_args()

    exclude_fields = set()
    if args.exclude:
        exclude_map = {
            'audio': 'audio_lang',
            'subtitles': 'subtitle_lang',
            'fps': 'fps_display',
            'codec': 'codec',
            'codec_profile': 'codec_profile',
            'filesize': 'filesize_display',
            'duration': 'duration_display',
            'interlace': 'interlace',
            'aspect': 'aspect',
            'resolution': 'resolution',
            'avg_bitrate': 'avg_bitrate_display',
            'max_bitrate': 'max_bitrate_display',
            'filename': 'filename'
        }
        for field in args.exclude.split(','):
            key = exclude_map.get(field.strip().lower())
            if key:
                exclude_fields.add(key)

    video_extensions = ['.mkv', '.mp4', '.mov', '.avi']
    video_files = []

    if args.recursive:
        for ext in video_extensions:
            video_files.extend(
                glob.glob(os.path.join(args.folder, '**', f'*{ext}'),
                          recursive=True)
            )
    else:
        for ext in video_extensions:
            video_files.extend(
                glob.glob(os.path.join(args.folder, f'*{ext}'))
            )

    if not video_files:
        print("No video files found.")
        return

    video_data_list = []
    with mp.Pool(workers) as pool:
        pbar = tqdm(
            pool.imap_unordered(process_video, video_files),
            total=len(video_files),
            desc="Analyzing",
            unit="file",
            ncols=35,
            leave=False,
            bar_format="{desc}{percentage:3.0f}% {bar} {n_fmt}/{total_fmt} "
        )
        for result in pbar:
            video_data_list.append(result)

        pbar.close()
    sys.stdout.write("\n")
    sys.stdout.flush()
    # Hard terminal reset on Linux / WSL
    if platform.system() == "Linux":
        os.system("reset")

    sort_key_map = {
        'filesize': 'filesize',
        'duration': 'duration',
        'avg_bitrate': 'avg_bitrate',
        'max_bitrate': 'max_bitrate',
        'fps': 'fps',
        'filename': 'filename',
        'codec': 'codec',
        'codec_profile': 'codec_profile',
        'interlace': 'interlace',
        'aspect': 'aspect',
        'resolution': 'resolution',
        'audio': 'audio_lang',
        'subtitles': 'subtitle_lang'
    }

    sort_key = sort_key_map.get(args.sort or 'filename')
    reverse = sort_key in ('filesize', 'duration', 'avg_bitrate', 'max_bitrate', 'fps')

    video_data_list.sort(
        key=lambda x: x.get(sort_key) or 0,
        reverse=reverse
    )

    if args.simple:
        # compute column widths dynamically from data
        name_w = max(len('Filename'), max(len(v['filename']) for v in video_data_list))
        size_w = max(len('Filesize'), max(len(v['filesize_display']) for v in video_data_list))
        dur_w  = max(len('Duration'), max(len(v['duration_display']) for v in video_data_list))

        sep = '  '

        print()
        print(f"{'Filename':<{name_w}}{sep}{'Filesize':<{size_w}}{sep}Duration")
        print('-' * (name_w + size_w + dur_w + len(sep) * 2))

        for v in video_data_list:
            print(
                f"{v['filename']:<{name_w}}{sep}"
                f"{v['filesize_display']:<{size_w}}{sep}"
                f"{v['duration_display']}"
            )

        print()
        return

    headers = {
        'filename': 'Filename',
        'filesize_display': 'Filesize',
        'duration_display': 'Duration',
        'codec': 'Codec',
        'codec_profile': 'Profile',
        'fps_display': 'FPS',
        'interlace': 'Interlace',
        'aspect': 'Aspect',
        'resolution': 'Resolution',
        'avg_bitrate_display': 'Avg Bitrate',
        'max_bitrate_display': 'Max Bitrate',
        'audio_lang': 'Audio',
        'subtitle_lang': 'Subtitles'
    }

    headers = {k: v for k, v in headers.items() if k not in exclude_fields}

    col_widths = {
        k: max(len(v), max(len(str(d.get(k, ''))) for d in video_data_list))
        for k, v in headers.items()
    }

    print()
    header_line = '  '.join(f"{headers[k]:<{col_widths[k]}}" for k in headers)
    print(header_line)
    print('-' * len(header_line))

    for d in video_data_list:
        print('  '.join(f"{str(d.get(k,'')):<{col_widths[k]}}" for k in headers))
    print()


if __name__ == '__main__':
    main()
