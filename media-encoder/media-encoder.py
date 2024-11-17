import os
import subprocess
import sys
import re


def get_video_dimensions(filename):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', filename]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"Error getting video dimensions for {filename}: {result.stderr}")
        return None, None
    try:
        width, height = map(int, result.stdout.strip().split('x'))
        return width, height
    except ValueError:
        print(f"Error parsing video dimensions for {filename}: {result.stdout}")
        return None, None


def calculate_output_dimensions(cropped_width, cropped_height, desired_ar):
    scale = False
    # First, try to fix output width as cropped_width
    output_width = cropped_width
    output_height = int(round(output_width / desired_ar))
    if output_height >= cropped_height:
        # Need to pad top and bottom
        pad_left = 0
        pad_right = 0
        pad_top = int((output_height - cropped_height) / 2)
        pad_bottom = output_height - cropped_height - pad_top
    else:
        # Try to fix output height as cropped_height
        output_height = cropped_height
        output_width = int(round(output_height * desired_ar))
        if output_width >= cropped_width:
            # Need to pad left and right
            pad_top = 0
            pad_bottom = 0
            pad_left = int((output_width - cropped_width) / 2)
            pad_right = output_width - cropped_width - pad_left
        else:
            # Output dimensions are smaller than cropped dimensions
            # Need to scale down the video
            scale = True
            output_width = int(round(min(cropped_width, output_width)))
            output_height = int(round(min(cropped_height, output_height)))
            pad_left = 0
            pad_right = 0
            pad_top = 0
            pad_bottom = 0
    return output_width, output_height, pad_left, pad_right, pad_top, pad_bottom, scale


def main():
    input_dir = 'input'
    media_extensions = ['.mkv', '.mp4', '.avi', '.webm']
    media_files = [os.path.join(input_dir, f) for f in os.listdir(input_dir)
                   if os.path.isfile(os.path.join(input_dir, f)) and os.path.splitext(f)[1].lower() in media_extensions]
    if not media_files:
        print("No media files found in the input directory.")
        sys.exit(1)

    crop_values = input("\nEnter crop values (left, right, top, bottom), e.g., '0,0,104,104': ")
    try:
        left, right, top, bottom = map(int, crop_values.split(','))
    except ValueError:
        print("Invalid crop values.")
        sys.exit(1)

    aspect_ratio = input("\nEnter output aspect ratio, e.g., '16:9': ")
    try:
        ar_width, ar_height = map(int, aspect_ratio.split(':'))
    except ValueError:
        print("Invalid aspect ratio.")
        sys.exit(1)

    codec_input = input("\nEnter codec (e.g., 'h264', 'h265', 'vp9', 'av1'): ").lower()
    print("""
Recommended values (1080p):
H.264 AVC Standard (no tune)   -  CRF 20
H.264 AVC Grain                -  CRF 22
H.265 HEVC Standard (no tune)  -  CRF 20
H.265 HEVC Grain               -  CRF 22
""")
    quality = input("Enter quality setting (CRF): ")

    # Map user-friendly codec names to ffmpeg encoder names
    codec_map = {
        'h264': 'libx264',
        'h265': 'libx265',
        'hevc': 'libx265',
        'vp9': 'libvpx-vp9',
        'av1': 'libaom-av1'
    }

    # Map codec to available tune options
    codec_tune_options = {
        'libx264': ['film', 'animation', 'grain', 'stillimage', 'fastdecode', 'zerolatency', 'psnr', 'ssim'],
        'libx265': ['grain', 'fastdecode', 'zerolatency', 'psnr', 'ssim'],
        'libvpx-vp9': [],
        'libaom-av1': ['ssim', 'psnr']
    }

    # Define encoder-specific options
    encoder_options = {
        'libx264': {
            'options': ['-bf', '4', '-rc-lookahead', '32', '-aq-mode', '3', '-b-pyramid', 'normal', '-coder', '1'],
            'pix_fmt': None,
        },
        'libx265': {
            'options': [],
            'pix_fmt': 'yuv420p10le',
        },
        'libvpx-vp9': {
            'options': [],
            'pix_fmt': None,
        },
        'libaom-av1': {
            'options': [],
            'pix_fmt': None,
        },
    }

    if codec_input not in codec_map:
        print("Unsupported codec detected. Please use one of the following codecs:")
        for key in codec_map.keys():
            print(f"- {key}")
        sys.exit(1)

    codec = codec_map[codec_input]
    available_tune_options = codec_tune_options.get(codec, [])

    desired_ar = ar_width / ar_height

    # Show available tune options based on selected codec
    if available_tune_options:
        print(f"\nAvailable tune options for {codec_input}: {', '.join(available_tune_options)}")
        tune_option = input("Enter tune setting (optional): ").lower()
        if tune_option and tune_option not in available_tune_options:
            print(f"Invalid tune option for codec {codec_input}. Available options are: {', '.join(available_tune_options)}")
            sys.exit(1)
    else:
        tune_option = ''
        print(f"No tune options available for codec {codec_input}.")
    print()

    # Ask for CPU usage percentage (this question is asked last)
    cpu_usage_percentage = input("Enter the maximum CPU usage percentage (e.g., '50' for 50%): ")

    # Validate CPU usage percentage and calculate number of threads
    try:
        cpu_usage_percentage = float(cpu_usage_percentage)
        if not 0 < cpu_usage_percentage <= 100:
            raise ValueError
    except ValueError:
        print("Invalid CPU usage percentage.")
        sys.exit(1)

    num_cores = os.cpu_count()
    if not num_cores:
        print("Unable to determine the number of CPU cores.")
        sys.exit(1)

    number_of_threads = max(1, int(num_cores * (cpu_usage_percentage / 100) // 4.5))
    print(f"\nUsing {number_of_threads} encoder thread(s) based on CPU usage percentage.")

    output_dir = 'output'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Substrings to replace with codec_display_name
    replace_substrings = ['HEVC', 'AVC', 'H.265', 'H.264', 'h264', 'h265', 'x264', 'x265']
    # Substrings to remove
    remove_substrings = ['REMUX']

    # Determine codec display name for filename replacements
    codec_display_name_map = {
        'libx264': 'AVC',
        'libx265': 'HEVC',
        'libvpx-vp9': 'VP9',
        'libaom-av1': 'AV1'
    }
    codec_display_name = codec_display_name_map.get(codec, codec_input.upper())

    for media_file in media_files:
        print(f"Processing file: {media_file}")
        # Get original dimensions
        orig_width, orig_height = get_video_dimensions(media_file)
        if orig_width is None or orig_height is None:
            continue  # skip this file
        # Compute cropped dimensions
        cropped_width = orig_width - left - right
        cropped_height = orig_height - top - bottom
        if cropped_width <= 0 or cropped_height <= 0:
            print(f"Cropped dimensions are invalid for file {media_file}. Skipping.")
            continue
        # Compute output dimensions and padding
        output_width, output_height, pad_left, pad_right, pad_top, pad_bottom, scale = calculate_output_dimensions(cropped_width, cropped_height, desired_ar)
        # Construct filter chain
        filter_chain = []
        # Crop filter
        crop_filter = f"crop=w=iw-{left}-{right}:h=ih-{top}-{bottom}:x={left}:y={top}"
        filter_chain.append(crop_filter)
        # Scale filter (if needed)
        if scale:
            scale_filter = f"scale=w={output_width}:h={output_height}"
            filter_chain.append(scale_filter)
        # Pad filter
        if pad_left > 0 or pad_right > 0 or pad_top > 0 or pad_bottom > 0:
            pad_filter = f"pad=w={output_width}:h={output_height}:x={pad_left}:y={pad_top}:color=black"
            filter_chain.append(pad_filter)
        # Build filter string
        filter_str = ",".join(filter_chain)
        # Build ffmpeg command to re-encode video only
        temp_video_file = os.path.join(output_dir, 'temp_' + os.path.basename(media_file))
        cmd_ffmpeg = [
            'ffmpeg', '-y', '-i', media_file,
            '-vf', filter_str,
            '-map', '0:v',  # Map only video
            '-c:v', codec,
            '-preset', 'slow',
            '-crf', quality,
            '-threads', str(number_of_threads),  # Limit CPU usage
        ]

        # Add pix_fmt if specified for the codec
        if encoder_options[codec]['pix_fmt']:
            cmd_ffmpeg.extend(['-pix_fmt', encoder_options[codec]['pix_fmt']])

        # Add encoder-specific options
        cmd_ffmpeg.extend(encoder_options[codec]['options'])

        # Add tune option if provided
        if tune_option:
            cmd_ffmpeg.extend(['-tune', tune_option])

        cmd_ffmpeg.append(temp_video_file)

        # Start video encoding
        print(f"Encoding video {media_file}...\n")
        process = subprocess.Popen(cmd_ffmpeg)
        process.wait()
        if process.returncode != 0:
            print(f"Error encoding video {media_file}")
            continue
        # Build output filename
        basename = os.path.splitext(os.path.basename(media_file))[0]
        # Replace substrings with codec_display_name
        for substring in replace_substrings:
            pattern = re.compile(re.escape(substring), re.IGNORECASE)
            basename = pattern.sub(codec_display_name, basename)
        # Remove substrings
        for substring in remove_substrings:
            pattern = re.compile(re.escape(substring), re.IGNORECASE)
            basename = pattern.sub('', basename)
        output_file = os.path.join(output_dir, basename + '.mkv')

        # Build mkvmerge command to merge re-encoded video with original audio and subtitles
        cmd_mkvmerge = [
            'mkvmerge',
            '-o', output_file,
            temp_video_file,
            '--no-video', media_file
        ]
        # Start merging process
        print(f"Merging video with audio and subtitles for {media_file}...")
        process = subprocess.Popen(cmd_mkvmerge)
        process.wait()
        if process.returncode != 0:
            print(f"Error merging files for {media_file}")
            continue
        # Delete temporary video file
        os.remove(temp_video_file)
        # Optionally delete original media file
        print(f"Finished processing {media_file}. Deleting original file.")
        os.remove(media_file)


if __name__ == "__main__":
    main()
