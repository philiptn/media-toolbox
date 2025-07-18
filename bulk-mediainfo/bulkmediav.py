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
    parser.add_argument('folder', nargs='?', default='.', help='Path to the folder containing video files, defaults to current folder.')
    parser.add_argument('-r', '--recursive', action='store_true', help='Recursively search subfolders for video files.')
    parser.add_argument('--sort', choices=[
        'filesize', 'codec', 'codec_profile', 'fps', 'interlace', 'aspect', 'resolution',
        'avg_bitrate', 'max_bitrate', 'filename', 'audio', 'subtitles'
    ], help='Column to sort the output by. Defaults to filename.')
    parser.add_argument('--exclude', help='Comma-separated list of fields to exclude from output (e.g. audio,subtitles,fps).')

    args = parser.parse_args()

    # Handle excluded fields
    exclude_fields = set()
    if args.exclude:
        exclude_map = {
            'audio': 'audio_lang',
            'subtitles': 'subtitle_lang',
            'fps': 'fps_display',
            'codec': 'codec',
            'codec_profile': 'codec_profile',
            'filesize': 'filesize_display',
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
            pattern = os.path.join(args.folder, '**', '*' + ext)
            video_files.extend(glob.glob(pattern, recursive=True))
    else:
        for ext in video_extensions:
            pattern = os.path.join(args.folder, '*' + ext)
            video_files.extend(glob.glob(pattern))

    if not video_files:
        print("No video files found in the specified folder.")
        return

    video_data_list = []
    total_files = len(video_files)

    spinner_chars = ['|', '/', '-', '\\']
    spinner_running = True

    print('\033[?25l', end='')

    current_file = 0

    def spinner():
        spinner_index = 0
        while spinner_running:
            progress_msg = f"Scanning file {current_file} of {total_files} {spinner_chars[spinner_index % len(spinner_chars)]}"
            print(progress_msg, end='\r', flush=True)
            spinner_index += 1
            time.sleep(0.1)

    spinner_thread = threading.Thread(target=spinner)
    spinner_thread.start()

    for idx, video_file in enumerate(video_files, start=1):
        current_file = idx
        media_info = MediaInfo.parse(video_file)

        codec = 'Unknown'
        codec_profile = 'Unknown'
        fps = 'Unknown'
        field_order = 'Unknown'
        aspect_ratio = 'Unknown'
        resolution = 'Unknown'
        avg_bitrate = 'Unknown'
        max_bitrate = 'Unknown'

        for track in media_info.tracks:
            if track.track_type == 'Video':
                codec = track.codec_id or 'Unknown'
                codec_profile = track.format_profile or 'Unknown'
                fps = track.frame_rate or 'Unknown'
                interlacing = track.scan_type or 'Unknown'
                if interlacing in ('Interlaced', 'MBAFF'):
                    field_order = track.scan_order or 'Unknown'
                else:
                    field_order = 'Progressive'
                if track.display_aspect_ratio:
                    try:
                        aspect_ratio_decimal = float(track.display_aspect_ratio)
                        aspect_ratio_fraction = Fraction(aspect_ratio_decimal).limit_denominator(100)
                        aspect_ratio = f"{aspect_ratio_fraction.numerator}:{aspect_ratio_fraction.denominator}"
                    except ValueError:
                        aspect_ratio = track.display_aspect_ratio
                elif track.width and track.height:
                    aspect_ratio_fraction = Fraction(track.width, track.height).limit_denominator(100)
                    aspect_ratio = f"{aspect_ratio_fraction.numerator}:{aspect_ratio_fraction.denominator}"
                resolution = f"{track.width}x{track.height}" if track.width and track.height else 'Unknown'
                avg_bitrate = track.bit_rate or 'Unknown'
                max_bitrate = track.maximum_bit_rate or 'Unknown'

                if avg_bitrate != 'Unknown':
                    avg_bitrate_mbps = int(avg_bitrate) / 1_000_000
                    avg_bitrate_display = f"{avg_bitrate_mbps:.2f} Mbps"
                else:
                    avg_bitrate_mbps = None
                    avg_bitrate_display = 'Unknown'

                if max_bitrate != 'Unknown':
                    max_bitrate_mbps = int(max_bitrate) / 1_000_000
                    max_bitrate_display = f"{max_bitrate_mbps:.2f} Mbps"
                else:
                    max_bitrate_mbps = None
                    max_bitrate_display = 'N/A'
                break

        audio_format_map = {
            'aac': 'AAC',
            'ac-3': 'AC3',
            'e-ac-3': 'EAC3',
            'dts': 'DTS',
            'truehd': 'THD',
            'mp3': 'MP3',
            'flac': 'FLAC',
            'pcm': 'PCM'
        }

        audio_tracks = [track for track in media_info.tracks if track.track_type == 'Audio']
        audio_langs = []
        for track in audio_tracks:
            lang = track.language or 'und'
            fmt_raw = (track.format or 'unknown').lower()
            fmt_mapped = audio_format_map.get(fmt_raw, fmt_raw)
            suffix = " (default)" if getattr(track, "default", "No") == "Yes" else ""
            entry = f"{lang.lower()}-{fmt_mapped.upper()}{suffix}"
            if entry not in audio_langs:
                audio_langs.append(entry)
        audio_lang = ', '.join(audio_langs) if audio_langs else 'und'

        subtitle_format_map = {
            'utf-8': 'srt',
            'subrip': 'srt',
            'pgs': 'sup',
            'hdmv_pgs': 'sup',
            'vobsub': 'sub',
            'ass': 'ass',
            'ssa': 'ssa',
            'mov_text': 'movtxt',
            'webvtt': 'vtt'
        }

        subtitle_tracks = [track for track in media_info.tracks if track.track_type == 'Text']
        if subtitle_tracks:
            subtitle_langs = []
            for track in subtitle_tracks:
                lang = track.language or 'und'
                fmt_raw = (track.format or 'unknown').lower()
                fmt_mapped = subtitle_format_map.get(fmt_raw, fmt_raw.lower())
                suffix = " (default)" if getattr(track, "default", "No") == "Yes" else ""
                entry = f"{lang.lower()}-{fmt_mapped.upper()}{suffix}"
                subtitle_langs.append(entry)
            subtitle_lang = ', '.join(subtitle_langs)
        else:
            subtitle_lang = ''

        filesize_bytes = os.path.getsize(video_file)
        filesize_sort = filesize_bytes
        if filesize_bytes >= 1024 ** 3:
            filesize_value = filesize_bytes / (1024 ** 3)
            filesize_unit = 'GB'
        else:
            filesize_value = filesize_bytes / (1024 ** 2)
            filesize_unit = 'MB'
        filesize_display = f"{filesize_value:.2f} {filesize_unit}"

        filename = os.path.basename(video_file)

        video_data = {
            'filesize': filesize_sort,
            'filesize_display': filesize_display,
            'codec': codec,
            'codec_profile': codec_profile,
            'fps': float(fps) if fps != 'Unknown' else None,
            'fps_display': str(fps),
            'interlace': field_order,
            'aspect': aspect_ratio,
            'resolution': resolution,
            'avg_bitrate': avg_bitrate_mbps,
            'avg_bitrate_display': avg_bitrate_display,
            'max_bitrate': max_bitrate_mbps,
            'max_bitrate_display': max_bitrate_display,
            'audio_lang': audio_lang,
            'subtitle_lang': subtitle_lang,
            'filename': filename
        }

        video_data_list.append(video_data)

    spinner_running = False
    spinner_thread.join()
    print('\033[?25h', end='')
    print(' ' * 80, end='\r')

    sort_key_map = {
        'filesize': 'filesize',
        'codec': 'codec',
        'codec_profile': 'codec_profile',
        'fps': 'fps',
        'interlace': 'interlace',
        'aspect': 'aspect',
        'resolution': 'resolution',
        'avg_bitrate': 'avg_bitrate',
        'max_bitrate': 'max_bitrate',
        'filename': 'filename',
        'audio': 'audio_lang',
        'subtitles': 'subtitle_lang'
    }
    sort_key_arg = args.sort or 'filename'
    sort_key = sort_key_map[sort_key_arg]

    if sort_key in ['filesize', 'avg_bitrate', 'max_bitrate']:
        descending = True
    else:
        descending = False

    if sort_key in ['filesize', 'avg_bitrate', 'max_bitrate', 'fps']:
        video_data_list.sort(key=lambda x: x[sort_key] if x[sort_key] is not None else -1, reverse=descending)
    else:
        video_data_list.sort(key=lambda x: x[sort_key] or '', reverse=descending)

    headers = {
        'filesize_display': 'Filesize',
        'codec': 'Codec',
        'codec_profile': 'Profile',
        'fps_display': 'FPS',
        'interlace': 'Interlace',
        'aspect': 'Aspect',
        'resolution': 'Resolution',
        'avg_bitrate_display': 'Avg Bitrate',
        'max_bitrate_display': 'Max Bitrate',
        'audio_lang': 'Audio',
        'subtitle_lang': 'Subtitles',
        'filename': 'Filename'
    }

    headers = {k: v for k, v in headers.items() if k not in exclude_fields}

    column_widths = {key: len(value) for key, value in headers.items()}
    for data in video_data_list:
        for key in column_widths.keys():
            value = str(data.get(key, ''))
            column_widths[key] = max(column_widths[key], len(value))

    format_string = ''
    header_string = ''
    for key in headers.keys():
        width = column_widths[key] + 2
        format_string += f"{{:<{width}}}"
        header_string += f"{headers[key]:<{width}}"

    print()
    print(header_string.strip())
    print('-' * len(header_string.strip()))

    for data in video_data_list:
        row_values = [str(data.get(key, '')) for key in headers.keys()]
        print(format_string.format(*row_values).strip())
    print()


if __name__ == '__main__':
    main()
