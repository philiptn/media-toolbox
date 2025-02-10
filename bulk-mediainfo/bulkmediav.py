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
    parser.add_argument('--sort', choices=['filesize', 'codec', 'fps', 'interlace', 'aspect', 'resolution', 'avg_bitrate', 'max_bitrate', 'filename'], help='Column to sort the output by. Defaults to filename.')
    args = parser.parse_args()

    video_extensions = ['.mkv', '.mp4', '.mov', '.avi']
    video_files = []

    if args.recursive:
        # Recursively search subfolders
        for ext in video_extensions:
            pattern = os.path.join(args.folder, '**', '*' + ext)
            video_files.extend(glob.glob(pattern, recursive=True))
    else:
        # Search only in the specified folder
        for ext in video_extensions:
            pattern = os.path.join(args.folder, '*' + ext)
            video_files.extend(glob.glob(pattern))

    if not video_files:
        print("No video files found in the specified folder.")
        return

    # List to store metadata dictionaries
    video_data_list = []
    total_files = len(video_files)

    # Spinner characters
    spinner_chars = ['|', '/', '-', '\\']
    spinner_running = True

    # Hide cursor
    print('\033[?25l', end='')

    current_file = 0  # Start at 0 so the spinner shows "Scanning file 1 of X"

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

        # Initialize metadata variables with default values
        codec = 'Unknown'
        fps = 'Unknown'
        field_order = 'Unknown'
        aspect_ratio = 'Unknown'
        resolution = 'Unknown'
        avg_bitrate = 'Unknown'
        max_bitrate = 'Unknown'

        # Process the first video track
        for track in media_info.tracks:
            if track.track_type == 'Video':
                codec = track.codec_id or 'Unknown'
                fps = track.frame_rate or 'Unknown'
                interlacing = track.scan_type or 'Unknown'  # 'Interlaced', 'Progressive', etc.
                if interlacing in ('Interlaced', 'MBAFF'):
                    field_order = track.scan_order or 'Unknown'  # 'TFF', 'BFF', etc.
                else:
                    field_order = 'Progressive'
                # Convert aspect ratio to fraction if it's a decimal
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
                else:
                    aspect_ratio = 'Unknown'
                resolution = f"{track.width}x{track.height}" if track.width and track.height else 'Unknown'
                avg_bitrate = track.bit_rate or 'Unknown'
                max_bitrate = track.maximum_bit_rate or 'Unknown'

                # Convert bitrate from bits per second to Mbps
                if avg_bitrate != 'Unknown':
                    avg_bitrate_mbps = int(avg_bitrate) / 1_000_000  # For sorting
                    avg_bitrate_display = f"{avg_bitrate_mbps:.2f} Mbps"
                else:
                    avg_bitrate_mbps = None
                    avg_bitrate_display = 'Unknown'

                if max_bitrate != 'Unknown':
                    max_bitrate_mbps = int(max_bitrate) / 1_000_000  # For sorting
                    max_bitrate_display = f"{max_bitrate_mbps:.2f} Mbps"
                else:
                    max_bitrate_mbps = None
                    max_bitrate_display = 'N/A'
                break  # Only process the first video track

        # Process audio track(s) to determine the main audio language.
        # Default is an empty string if nothing is found.
        audio_lang = ""
        for track in media_info.tracks:
            if track.track_type == 'Audio':
                # If a default audio track is flagged, use its language.
                if getattr(track, 'default', None) in ['Yes', '1']:
                    if track.language is not None:
                        audio_lang = track.language
                    break
                # Otherwise, if language info is present, use the first encountered audio track.
                if track.language:
                    audio_lang = track.language

        # Process subtitle track(s) to determine the main subtitle language.
        subtitle_lang = ""
        for track in media_info.tracks:
            if track.track_type == 'Text':
                if getattr(track, 'default', None) in ['Yes', '1']:
                    if track.language is not None:
                        subtitle_lang = track.language
                    break
                if track.language:
                    subtitle_lang = track.language

        # Get file size in bytes
        filesize_bytes = os.path.getsize(video_file)
        filesize_sort = filesize_bytes  # For sorting purposes
        if filesize_bytes >= 1024 ** 3:
            filesize_value = filesize_bytes / (1024 ** 3)
            filesize_unit = 'GB'
        else:
            filesize_value = filesize_bytes / (1024 ** 2)
            filesize_unit = 'MB'
        filesize_display = f"{filesize_value:.2f} {filesize_unit}"

        # Use only the filename without any folder paths
        filename = os.path.basename(video_file)

        # Store all data in a dictionary
        video_data = {
            'filesize': filesize_sort,  # For sorting (bytes)
            'filesize_display': filesize_display,
            'codec': codec,
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

    # Stop spinner and restore cursor
    spinner_running = False
    spinner_thread.join()
    print('\033[?25h', end='')

    # Clear the progress line
    print(' ' * 80, end='\r')

    # Set default sort key to 'filename' if no sort option is provided
    sort_key = args.sort or 'filename'
    if sort_key in ['filesize', 'avg_bitrate', 'max_bitrate']:
        descending = True
    else:
        descending = False

    # Special handling for numeric sorting keys
    if sort_key in ['filesize', 'avg_bitrate', 'max_bitrate', 'fps']:
        video_data_list.sort(key=lambda x: x[sort_key] if x[sort_key] is not None else -1, reverse=descending)
    else:
        video_data_list.sort(key=lambda x: x[sort_key] or '', reverse=descending)

    # Update headers to include Audio and Subtitle columns.
    headers = {
        'filesize_display': 'Filesize',
        'codec': 'Codec',
        'fps_display': 'FPS',
        'interlace': 'Interlace',
        'aspect': 'Aspect',
        'resolution': 'Resolution',
        'avg_bitrate_display': 'Avg Bitrate',
        'max_bitrate_display': 'Max Bitrate',
        'audio_lang': 'Audio',
        'subtitle_lang': 'Subtitle',
        'filename': 'Filename'
    }

    # Determine the maximum width for each column based on header and data lengths.
    column_widths = {key: len(value) for key, value in headers.items()}
    for data in video_data_list:
        for key in column_widths.keys():
            value = str(data.get(key, ''))
            column_widths[key] = max(column_widths[key], len(value))

    # Build the format string dynamically.
    format_string = ''
    header_string = ''
    for key in headers.keys():
        width = column_widths[key] + 2  # Add some padding.
        format_string += f"{{:<{width}}}"
        header_string += f"{headers[key]:<{width}}"

    # Print the header and a separator.
    print()
    print(header_string.strip())
    print('-' * len(header_string.strip()))

    # Print the data rows.
    for data in video_data_list:
        print(format_string.format(
            data['filesize_display'],
            data['codec'],
            data['fps_display'],
            data['interlace'],
            data['aspect'],
            data['resolution'],
            data['avg_bitrate_display'],
            data['max_bitrate_display'],
            data['audio_lang'],
            data['subtitle_lang'],
            data['filename']
        ).strip())
    print()


if __name__ == '__main__':
    main()
