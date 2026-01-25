import argparse
import os
import glob
import sys
import time
from pymediainfo import MediaInfo
from fractions import Fraction
import threading


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
    total_files = len(video_files)
    spinner_running = True
    spinner_chars = ['|', '/', '-', '\\']
    current_file = 0

    print('\033[?25l', end='')

    def spinner():
        i = 0
        while spinner_running:
            print(
                f"Scanning file {current_file} of {total_files} "
                f"{spinner_chars[i % 4]}",
                end='\r',
                flush=True
            )
            i += 1
            time.sleep(0.1)

    spinner_thread = threading.Thread(target=spinner)
    spinner_thread.start()

    for idx, video_file in enumerate(video_files, start=1):
        current_file = idx
        media_info = MediaInfo.parse(video_file)

        codec = codec_profile = fps = 'Unknown'
        field_order = aspect_ratio = resolution = 'Unknown'
        avg_bitrate = max_bitrate = None
        duration_seconds = None
        duration_display = 'Unknown'

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

        audio_tracks = [t for t in media_info.tracks if t.track_type == 'Audio']
        audio_lang = ', '.join(
            f"{(t.language or 'und').lower()}-{(t.format or '').upper()}"
            for t in audio_tracks
        )

        subtitle_tracks = [t for t in media_info.tracks if t.track_type == 'Text']
        subtitle_lang = ', '.join(
            f"{(t.language or 'und').lower()}-{(t.format or '').upper()}"
            for t in subtitle_tracks
        )

        filesize_bytes = os.path.getsize(video_file)
        if filesize_bytes >= 1024 ** 3:
            filesize_display = f"{filesize_bytes / (1024 ** 3):.2f} GB"
        else:
            filesize_display = f"{filesize_bytes / (1024 ** 2):.2f} MB"

        video_data_list.append({
            'filename': os.path.basename(video_file),
            'filesize': filesize_bytes,
            'filesize_display': filesize_display,
            'duration': duration_seconds,
            'duration_display': duration_display,
            'codec': codec,
            'codec_profile': codec_profile,
            'fps': float(fps) if fps != 'Unknown' else None,
            'fps_display': str(fps),
            'interlace': field_order,
            'aspect': aspect_ratio,
            'resolution': resolution,
            'avg_bitrate': avg_bitrate,
            'avg_bitrate_display': f"{avg_bitrate:.2f} Mbps" if avg_bitrate else 'Unknown',
            'max_bitrate': max_bitrate,
            'max_bitrate_display': f"{max_bitrate:.2f} Mbps" if max_bitrate else 'N/A',
            'audio_lang': audio_lang,
            'subtitle_lang': subtitle_lang
        })

    spinner_running = False
    spinner_thread.join()
    print('\033[?25h', end='')
    print(' ' * 200, end='\r')

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
    reverse = sort_key in ('filesize', 'duration', 'avg_bitrate', 'max_bitrate')

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
