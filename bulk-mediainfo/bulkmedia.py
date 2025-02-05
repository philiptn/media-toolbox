import os
import argparse
import subprocess
import json
import time
import re
from datetime import datetime

# Define color constants
GREY = '\033[90m'
RESET = '\033[0m'
BLUE = '\033[94m'


def get_timestamp():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def format_tracks_as_blocks(json_data, line_width=80):
    formatted_blocks = []
    for track in json_data.get('tracks', []):  # Safely access 'tracks'
        line = ""
        block = []
        for key, value in track.items():
            # Handling None values to be printed as 'null'
            value_repr = 'null' if value is None else f"'{value}'" if isinstance(value, str) else str(value)
            entry = f"{key}: {value_repr}, "
            if len(line + entry) > line_width:
                block.append(line.rstrip())
                line = ""
            line += entry
        block.append(line.rstrip())  # Add remaining data to the block
        formatted_blocks.append('\n'.join(block))

    return '\n\n'.join(formatted_blocks)


# Function to simplify the JSON structure
def simplify_json(data, fields_to_keep):
    simplified = {key: data[key] for key in fields_to_keep if key in data}
    simplified['tracks'] = [
        {
            'id': track.get('id'),
            'type': track.get('type'),
            'codec_name': track.get('codec'),
            'language': track.get('properties', {}).get('language'),
            'track_name': track.get('properties', {}).get('track_name'),
            'default_track': track.get('properties', {}).get('default_track'),
            'forced_track': track.get('properties', {}).get('forced_track', False),
            'codec_id': track.get('properties', {}).get('codec_id')
        } for track in data.get('tracks', [])
    ]
    return simplified


def boxify(text):
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    lines = text.split('\n')
    # Remove any empty lines at the start and end
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Compute the maximum line width without ANSI codes
    stripped_lines = [ansi_escape.sub('', line) for line in lines]
    max_line_length = max(len(line) for line in stripped_lines)
    # Prepare the box
    top_line = '╭' + '―' * (max_line_length + 2) + '╮'
    bottom_line = '╰' + '―' * (max_line_length + 2) + '╯'
    # Add vertical bars to each line
    boxed_lines = [top_line]
    for original_line, stripped_line in zip(lines, stripped_lines):
        padding_needed = max_line_length - len(stripped_line)
        # Add spaces to the end of the original line
        padded_line = original_line + ' ' * padding_needed
        boxed_line = '│ ' + padded_line + ' │'
        boxed_lines.append(boxed_line)
    boxed_lines.append(bottom_line)
    return '\n'.join(boxed_lines)


def get_mkv_info(debug, filename, silent):
    command = ["mkvmerge", "-J", filename]
    done = False
    result = None
    printed = False
    while not done:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            time.sleep(5)
        if result.returncode == 0:
            done = True

    # Parse the JSON output and pretty-print it
    parsed_json = json.loads(result.stdout)
    pretty_json = json.dumps(parsed_json, indent=2)

    # Simplifying the JSON
    fields_to_keep = ['file_name', 'tracks']
    simplified_json = simplify_json(parsed_json, fields_to_keep)
    compact_json = format_tracks_as_blocks(simplified_json, 70)

    # Function to colorize text
    def colorize(text):
        colored_text = ""
        for line in text.split('\n'):
            line_colored = ''
            for part in line.split(', '):
                if ':' in part:
                    key, value = part.split(':', 1)
                    line_colored += f"{BLUE}{key}{RESET}: {value.strip()}, "
            colored_text += line_colored.rstrip(', ') + '\n'
        return colored_text

    colored_text = colorize(compact_json)

    # Prepare content to be boxed
    file_name_only = os.path.basename(filename)
    content = file_name_only + '\n\n' + colored_text

    # Boxify the content
    boxed_content = boxify(content)

    # Print the boxed content
    print('\n' + boxed_content)

    return parsed_json, pretty_json


def find_mkv_files(folder, recursive=False):
    mkv_files = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            for file in files:
                if file.lower().endswith('.mkv'):
                    mkv_files.append(os.path.join(root, file))
    else:
        for file in os.listdir(folder):
            if file.lower().endswith('.mkv'):
                mkv_files.append(os.path.join(folder, file))
    return mkv_files


def main():
    parser = argparse.ArgumentParser(description='Scan a folder and print MKV media info.')
    parser.add_argument('folder', nargs='?', default='.', help='Folder to scan')
    parser.add_argument('-r', '--recursive', action='store_true', help='Recursively scan subfolders')
    parser.add_argument('--debug', action='store_true', help='Print debug information')
    args = parser.parse_args()

    folder_to_scan = args.folder
    recursive = args.recursive

    mkv_files = find_mkv_files(folder_to_scan, recursive)

    if not mkv_files:
        print(f"No MKV files found in folder {folder_to_scan}")
        return

    for mkv_file in mkv_files:
        get_mkv_info(debug=args.debug, filename=mkv_file, silent=False)
    print()


if __name__ == '__main__':
    main()

