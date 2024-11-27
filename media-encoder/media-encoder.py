import os
import subprocess
import sys
import re
import shutil  # Added to enable directory removal


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


def natural_sort_key(s):
    """
    Generates a key for natural sorting.
    Splits the string into a list of integers and lowercase strings.
    """
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)]  # Raw string to fix SyntaxWarning


def delete_empty_media_dirs(media_dir, input_dir, media_extensions):
    media_dir = os.path.abspath(media_dir)
    input_dir = os.path.abspath(input_dir)
    while media_dir != input_dir:
        # Check if media_dir has any media files
        has_media_files = False
        for item in os.listdir(media_dir):
            item_path = os.path.join(media_dir, item)
            if os.path.isfile(item_path) and os.path.splitext(item)[1].lower() in media_extensions:
                has_media_files = True
                break
        if has_media_files:
            # There are media files left, stop
            break
        else:
            # No media files, delete directory regardless of other files
            shutil.rmtree(media_dir)
            print(f"Deleted directory {media_dir}")
            # Move up one level
            media_dir = os.path.dirname(media_dir)
            media_dir = os.path.abspath(media_dir)
    return


def main():
    input_dir = 'input'
    output_dir = 'output'
    media_extensions = ['.mkv', '.mp4', '.avi', '.webm']

    # Collect all media files recursively
    media_files = []
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            if os.path.splitext(file)[1].lower() in media_extensions:
                full_path = os.path.join(root, file)
                media_files.append(full_path)

    if not media_files:
        print("No media files found in the input directory.")
        sys.exit(1)

    # Sort media_files using natural sort
    media_files_sorted = sorted(media_files, key=lambda x: natural_sort_key(os.path.relpath(x, input_dir)))

    # **Optional Cropping**
    print()
    done = False
    while not done:
        perform_cropping = input("Do you want to crop the video stream? (yes/no): ").strip().lower()
        if perform_cropping in ['yes', 'y']:
            done = True
            crop_values = input("Enter crop values (left, right, top, bottom), e.g., '0,0,104,104': ")
            try:
                left, right, top, bottom = map(int, crop_values.split(','))
                cropping = True
            except ValueError:
                print("Invalid crop values. Exiting.")
                sys.exit(1)
        elif perform_cropping in ['no', 'n']:
            done = True
            cropping = False
            left = right = top = bottom = 0  # Defaults, won't affect cropping if not used

    # **Optional Aspect Ratio Resizing**
    done = False
    while not done:
        perform_resize = input("Do you want to resize the video stream to a specific aspect ratio? (yes/no): ").strip().lower()
        if perform_resize in ['yes', 'y']:
            done = True
            aspect_ratio = input("Enter output aspect ratio, e.g., '16:9': ")
            try:
                ar_width, ar_height = map(int, aspect_ratio.split(':'))
                desired_ar = ar_width / ar_height
                resizing = True
            except ValueError:
                print("Invalid aspect ratio. Exiting.")
                sys.exit(1)
        elif perform_resize in ['no', 'n']:
            done = True
            resizing = False
            desired_ar = None  # Indicates no resizing

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
        'libx265': ['grain', 'fastdecode', 'zerolatency', 'psnr', 'ssim', 'animation'],
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
            'options': ['-x265-params', 'rc-lookahead=32:aq-mode=3:bframes=4'],
            'pix_fmt': None,
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

    codec_input = input("Enter output codec (e.g., 'h264', 'h265', 'vp9', 'av1'): ").lower()
    if codec_input not in codec_map:
        print("Unsupported codec detected. Please use one of the following codecs:")
        for key in codec_map.keys():
            print(f"- {key}")
        sys.exit(1)

    codec = codec_map[codec_input]
    available_tune_options = codec_tune_options.get(codec, [])

    print("""
Recommended values (1080p):
H.264 AVC Standard (no tune)                -  CRF 20
H.264 AVC Grain                             -  CRF 22
H.265 HEVC Standard (no tune)               -  CRF 20
H.265 HEVC Animation (minimizes artifacts)  -  CRF 20
H.265 HEVC Grain                            -  CRF 22
""")
    quality = input("Enter quality setting (CRF): ")

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
        if not 0 < cpu_usage_percentage <= 1000:
            raise ValueError
    except ValueError:
        print("Invalid CPU usage percentage.")
        sys.exit(1)

    num_cores = os.cpu_count()
    if not num_cores:
        print("Unable to determine the number of CPU cores.")
        sys.exit(1)

    if codec.lower() == "libx265":
        divisor = 4.5
    else:
        divisor = 1
    number_of_threads = max(1, int(num_cores * (cpu_usage_percentage / 100) // divisor))
    print(f"\nUsing {number_of_threads} encoder thread(s) based on CPU usage percentage.")

    # Ensure output directory exists
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Substrings to replace with codec_display_name
    replace_substrings = ['HEVC', 'AVC', 'H.265', 'H.264', 'h264', 'h265', 'x264', 'x265']
    # Substrings to remove
    remove_substrings = ['REMUX']

    # Determine codec display name for filename replacements
    codec_display_name_map = {
        'libx264': 'x264',
        'libx265': 'x265',
        'libvpx-vp9': 'VP9',
        'libaom-av1': 'AV1'
    }
    codec_display_name = codec_display_name_map.get(codec, codec_input.upper())

    for media_file in media_files_sorted:
        print(f"\nProcessing file: {media_file}")
        # Get original dimensions
        orig_width, orig_height = get_video_dimensions(media_file)
        if orig_width is None or orig_height is None:
            continue  # skip this file

        # **Compute Cropped Dimensions (if cropping is enabled)**
        if cropping:
            cropped_width = orig_width - left - right
            cropped_height = orig_height - top - bottom
            if cropped_width <= 0 or cropped_height <= 0:
                print(f"Cropped dimensions are invalid for file {media_file}. Skipping.")
                continue
        else:
            cropped_width = orig_width
            cropped_height = orig_height

        # **Compute Output Dimensions and Padding (if resizing is enabled)**
        if resizing:
            output_width, output_height, pad_left, pad_right, pad_top, pad_bottom, scale = calculate_output_dimensions(cropped_width, cropped_height, desired_ar)
        else:
            # If no resizing, output dimensions are the same as cropped dimensions
            output_width = cropped_width
            output_height = cropped_height
            pad_left = pad_right = pad_top = pad_bottom = 0
            scale = False

        # **Construct Filter Chain Based on User Choices**
        filter_chain = []
        if cropping:
            # Crop filter
            crop_filter = f"crop=w=iw-{left}-{right}:h=ih-{top}-{bottom}:x={left}:y={top}"
            filter_chain.append(crop_filter)
        if resizing:
            if scale:
                # Scale filter
                scale_filter = f"scale=w={output_width}:h={output_height}"
                filter_chain.append(scale_filter)
            # Pad filter
            if pad_left > 0 or pad_right > 0 or pad_top > 0 or pad_bottom > 0:
                pad_filter = f"pad=w={output_width}:h={output_height}:x={pad_left}:y={pad_top}:color=black"
                filter_chain.append(pad_filter)
        # Build filter string
        filter_str = ",".join(filter_chain) if filter_chain else None

        # **Build FFmpeg Command to Re-encode Video Only**
        # Determine relative path
        rel_path = os.path.relpath(media_file, input_dir)
        rel_dir = os.path.dirname(rel_path)
        # Create corresponding directory in output_dir
        output_subdir = os.path.join(output_dir, rel_dir)
        if not os.path.exists(output_subdir):
            os.makedirs(output_subdir)

        temp_video_file = os.path.join(output_subdir, 'temp_' + os.path.basename(media_file))
        cmd_ffmpeg = [
            'ffmpeg', '-y', '-i', media_file
        ]

        if filter_str:
            cmd_ffmpeg.extend(['-vf', filter_str])

        cmd_ffmpeg.extend([
            '-map', '0:v',  # Map only video
            '-c:v', codec,
            '-preset', 'slow',
            '-crf', quality,
            '-threads', str(number_of_threads),  # Limit CPU usage
        ])

        # Add pix_fmt if specified for the codec
        if encoder_options[codec]['pix_fmt']:
            cmd_ffmpeg.extend(['-pix_fmt', encoder_options[codec]['pix_fmt']])

        # Add encoder-specific options
        cmd_ffmpeg.extend(encoder_options[codec]['options'])

        # Add tune option if provided
        if tune_option:
            cmd_ffmpeg.extend(['-tune', tune_option])

        cmd_ffmpeg.append(temp_video_file)

        # **Start Video Encoding**
        print(f"Encoding video {media_file}...\n")
        try:
            subprocess.run(cmd_ffmpeg, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error encoding video {media_file}:\n{e.stderr}")
            continue

        # **Build Output Filename**
        basename = os.path.splitext(os.path.basename(media_file))[0]
        # Replace substrings with codec_display_name
        for substring in replace_substrings:
            pattern = re.compile(re.escape(substring), re.IGNORECASE)
            basename = pattern.sub(codec_display_name, basename)
        # Remove substrings
        for substring in remove_substrings:
            pattern = re.compile(re.escape(substring), re.IGNORECASE)
            basename = pattern.sub('', basename)
        output_file = os.path.join(output_subdir, basename + '.mkv')

        # **Build MKVMerge Command to Merge Re-encoded Video with Original Audio and Subtitles**
        cmd_mkvmerge = [
            'mkvmerge',
            '-o', output_file,
            temp_video_file,
            '--no-video', media_file
        ]
        # **Start Merging Process**
        print(f"Merging video with audio and subtitles for {media_file}...")
        try:
            subprocess.run(cmd_mkvmerge, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error merging files for {media_file}:\n{e.stderr}")
            continue

        # **Delete Temporary Video File**
        os.remove(temp_video_file)

        print(f"Finished processing {media_file}. Deleting original file.")
        os.remove(media_file)

        # **Delete Empty Media Directories**
        media_dir = os.path.dirname(media_file)
        delete_empty_media_dirs(media_dir, input_dir, media_extensions)

    print("\nProcessing complete.")


if __name__ == "__main__":
    main()
