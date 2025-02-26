import os
import subprocess
import sys
import re
import shutil  # Added to enable directory removal
import platform
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from prompt_toolkit import prompt
from better_ffmpeg_progress import FfmpegProcess
from rich.console import Console


# Calculate max_workers as 85% of the available logical cores
max_cpu_usage = 85
max_workers = int(os.cpu_count() * int(max_cpu_usage) / 100)


if platform.system() == "Windows":
    # Update PATH to point to FFmpeg in bin folder if running Windows.
    # Needed for better-ffmpeg-progress to work properly.
    ffmpeg_dir = os.path.abspath(r'.bin\ffmpeg')
    os.environ["PATH"] = os.pathsep.join([ffmpeg_dir, os.environ.get("PATH", "")])
    ffmpeg = r'.bin\ffmpeg\ffmpeg.exe'
    ffprobe = r'.bin\ffmpeg\ffprobe.exe'
    mkvmerge = r'.bin\mkvtoolnix\mkvmerge.exe'
else:
    ffmpeg = 'ffmpeg'
    ffprobe = 'ffprobe'
    mkvmerge = 'mkvmerge'


def get_video_dimensions(filename):
    cmd = [ffprobe, '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', filename]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"Error getting video dimensions for {filename}: {result.stderr}")
        return None, None
    try:
        # Strip any trailing 'x' and whitespace
        output = result.stdout.strip().rstrip('x')
        width, height = map(int, output.split('x'))
        return width, height
    except ValueError:
        print(f"Error parsing video dimensions for {filename}: {result.stdout}")
        return None, None


def get_all_files(path):
    files = []
    for dirpath, dirnames, filenames in os.walk(path):
        # Modify dirnames in-place to skip directories starting with a dot
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        files.extend(os.path.join(dirpath, f) for f in filenames if not f.startswith('.'))
    return files


def wait_for_stable_files(path):
    def is_file_stable(file_path):
        """Check if a file's size is stable (indicating it is fully copied)."""
        initial_size = os.path.getsize(file_path)
        time.sleep(2.5)
        new_size = os.path.getsize(file_path)
        return initial_size == new_size

    stable_files = set()

    while True:
        # Get the current list of files to check
        files = []
        for dirpath, dirnames, filenames in os.walk(path):
            # Modify dirnames in-place to skip directories starting with a dot
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            files.extend(os.path.join(dirpath, f) for f in filenames if not f.startswith('.'))

        def process_file(file_path):
            if file_path in stable_files:
                return None  # Skip already stable files
            if is_file_stable(file_path):
                return file_path  # Return stable file
            return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {executor.submit(process_file, file): file for file in files if file not in stable_files}

            for future in as_completed(future_to_file):
                result = future.result()
                if result:
                    stable_files.add(result)

        # Check again
        time.sleep(2.5)
        files = []
        for dirpath, dirnames, filenames in os.walk(path):
            # Modify dirnames in-place to skip directories starting with a dot
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            files.extend(os.path.join(dirpath, f) for f in filenames if not f.startswith('.'))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {executor.submit(process_file, file): file for file in files if file not in stable_files}

            for future in as_completed(future_to_file):
                result = future.result()
                if result:
                    stable_files.add(result)

        if len(stable_files) >= len(files):
            break  # Exit if all files are stable

    return len(stable_files)


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
            # Move up one level
            media_dir = os.path.dirname(media_dir)
            media_dir = os.path.abspath(media_dir)
    return


def main():
    input_dir = 'input'
    output_dir = 'output'
    media_extensions = ['.mkv', '.mp4', '.avi', '.webm']

    all_files = get_all_files(input_dir)
    if not all_files:
        exit(2)

    # **Optional Cropping**
    done = False
    while not done:
        perform_cropping = prompt("\nDo you want to crop the video stream? (yes/no): ", default="no")
        if perform_cropping in ['yes', 'y']:
            done = True
            crop_values = prompt("\nEnter crop values (left, right, top, bottom): ", default="0,0,104,104")
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
        perform_resize = prompt("\nDo you want to resize the video stream to a specific aspect ratio? (yes/no): ", default="no")
        if perform_resize in ['yes', 'y']:
            done = True
            aspect_ratio = prompt("\nEnter output aspect ratio: ", default="16:9")
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
            # -bf 4: Use up to 4 consecutive B-frames, increasing compression efficiency by referencing more frames.
            # -rc-lookahead 32: Pre-scan 32 upcoming frames to allocate bits more effectively, improving scene transitions.
            # -aq-mode 3: Employ advanced adaptive quantization, giving more bits to complex areas for clearer detail.
            # -b-pyramid normal: Allow B-frames to serve as references for other frames, boosting overall compression.
            # -coder 1: Enable CABAC entropy coding, improving compression without sacrificing quality.
            'options': ['-bf', '4', '-rc-lookahead', '32', '-aq-mode', '3', '-b-pyramid', 'normal', '-coder', '1'],
            'pix_fmt': None,
        },
        'libx265': {
            # rc-lookahead=32: Analyze the next 32 frames to smoothly manage bitrate and maintain consistent quality.
            # aq-mode=3: Deploy a more complex AQ scheme that redistributes bitrate for sharper detail in challenging areas.
            # bframes=4: Insert up to 4 consecutive B-frames, enabling higher compression ratios with minimal quality loss.
            'options': ['-x265-params', 'rc-lookahead=32:aq-mode=3:bframes=4:no-sao=1'],
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

    codec_input = prompt("\nEnter output codec (e.g., 'h264', 'h265', 'vp9', 'av1'): ", default="h265")
    if codec_input not in codec_map:
        print("Unsupported codec detected. Please use one of the following codecs:")
        for key in codec_map.keys():
            print(f"- {key}")
        sys.exit(1)

    codec = codec_map[codec_input]
    available_tune_options = codec_tune_options.get(codec, [])

    # Show available tune options based on selected codec
    if available_tune_options:
        default_tune = ''
        print("\nUse tune 'grain' if you want to preserve all the high level details (at the cost of larger filesize)")
        print(f"Available tune options for {codec_input}: {', '.join(available_tune_options)}")
        tune_option = prompt("Enter tune setting (optional): ", default=default_tune)
        if tune_option and tune_option not in available_tune_options:
            print(f"Invalid tune option for codec {codec_input}. Available options are: {', '.join(available_tune_options)}")
            sys.exit(1)
    else:
        tune_option = ''
        print(f"No tune options available for codec {codec_input}.")

    if tune_option == 'grain':
        quality_default = '20'
    else:
        quality_default = '18'
    quality = prompt("\nEnter quality setting (CRF): ", default=quality_default)

    encoder_speed = None
    if codec in ['libx264', 'libx265']:
        if tune_option == 'grain':
            speed_default = 'slow'
        else:
            speed_default = 'medium'
        valid_speeds = ["slow", "medium"]
        print(f"\nAvailable speed options for {codec_input}: {', '.join(valid_speeds)}")
        print("'slow'      - Preserves the most details, but takes a long time to encode")
        print("'medium'    - Significantly faster but falls off in the smaller details")
        encoder_speed = prompt(
            f"\nEnter encoder speed: ",
            default=speed_default
        )
        if encoder_speed not in valid_speeds:
            print("Invalid speed/preset choice. Exiting.")
            sys.exit(1)
    elif codec == 'libvpx-vp9':
        valid_speeds = [f"'{str(i)}'" for i in range(9)]  # 0 through 8
        print(f"Available speed options for {codec_input}: {', '.join(valid_speeds)}")
        encoder_speed = prompt(
            f"Enter encoder speed: ",
            default="4"
        )
        if encoder_speed not in valid_speeds:
            print("Invalid speed (cpu-used) choice. Exiting.")
            sys.exit(1)
    elif codec == 'libaom-av1':
        valid_speeds = [f"'{str(i)}'" for i in range(9)]  # 0 through 8
        print(f"Available speed options for {codec_input}: {', '.join(valid_speeds)}")
        encoder_speed = prompt(
            f"Encoder speed: ",
            default="4"
        )
        if encoder_speed not in valid_speeds:
            print("Invalid speed (cpu-used) choice. Exiting.")
            sys.exit(1)

    if codec in ['libx264', 'libx265']:
        if codec == 'libx264':
            encoder_options[codec]['options'].extend(['-psy-rd', '3.0:0.0'])
        elif codec == 'libx265':
            for i, opt in enumerate(encoder_options[codec]['options']):
                if opt == '-x265-params':
                    encoder_options[codec]['options'][i + 1] += ':psy-rd=3:psy-rdoq=3'
                    break

    cpu_usage_percentage = prompt("\nEnter the maximum CPU usage percentage (e.g., '50' for 50%): ", default="auto")

    # Validate CPU usage percentage and calculate number of threads
    if cpu_usage_percentage == "auto":
        number_of_threads = 0
    else:
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
            divisor = 0.8
        number_of_threads = max(1, int(num_cores * (cpu_usage_percentage / 100) // divisor))
        # Limit to 16 threads based on x264 documentation
        if codec.lower() == "libx264":
            number_of_threads = min(16, number_of_threads)
        print(f"\nUsing {number_of_threads} encoder thread(s) based on CPU usage percentage.")

    ffmpeg_ui = prompt("\nSelect preferred FFmpeg UI (compact, advanced): ", default="compact")

    # Ensure output directory exists
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Substrings to replace with codec_display_name
    replace_substrings = ['HEVC', 'AVC', 'H.265', 'H.264', 'h264', 'h265', 'x264', 'x265', 'VC-1']
    # Substrings to remove
    remove_substrings = ['.REMUX', ' REMUX', 'REMUX']

    # Determine codec display name for filename replacements
    codec_display_name_map = {
        'libx264': 'x264',
        'libx265': 'x265',
        'libvpx-vp9': 'VP9',
        'libaom-av1': 'AV1'
    }
    codec_display_name = codec_display_name_map.get(codec, codec_input.upper())

    remaining_files = wait_for_stable_files(input_dir)
    while remaining_files:
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

        for media_file in media_files_sorted:
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
                ffmpeg, '-y', '-i', media_file
            ]

            if filter_str:
                cmd_ffmpeg.extend(['-vf', filter_str])

            cmd_ffmpeg.extend([
                '-map', 'v:0',  # Map only video
                '-c:v', codec,
                '-crf', quality,
                '-threads', str(number_of_threads),  # Limit CPU usage
            ])

            # Apply the encoder speed/preset depending on the codec
            if codec in ['libx264', 'libx265']:
                # Use '-preset'
                cmd_ffmpeg.extend(['-preset', encoder_speed])
            elif codec == 'libvpx-vp9':
                # For VP9, use '-cpu-used'
                cmd_ffmpeg.extend(['-cpu-used', encoder_speed])
            elif codec == 'libaom-av1':
                # For AV1, also use '-cpu-used'
                cmd_ffmpeg.extend(['-cpu-used', encoder_speed])

            # Add pix_fmt if specified for the codec
            if encoder_options[codec]['pix_fmt']:
                cmd_ffmpeg.extend(['-pix_fmt', encoder_options[codec]['pix_fmt']])

            # Add encoder-specific options
            cmd_ffmpeg.extend(encoder_options[codec]['options'])

            # Add tune option if provided
            if tune_option:
                cmd_ffmpeg.extend(['-tune', tune_option])

            cmd_ffmpeg.append(temp_video_file)

            if ffmpeg_ui.lower() == "compact":
                # **Start Video Encoding**
                null_device = "/dev/null" if os.name != "nt" else "NUL"
                console = Console()
                console.print(f"\n{' '.join(cmd_ffmpeg)}\n", style="bold bright_black", highlight=False)
                process = FfmpegProcess(cmd_ffmpeg, ffmpeg_log_file=null_device)
                return_code = process.run()
            else:
                console = Console()
                console.print(f"\n{' '.join(cmd_ffmpeg)}\n", style="bold bright_black", highlight=False)
                try:
                    subprocess.run(cmd_ffmpeg, check=True)
                except subprocess.CalledProcessError as e:
                    print(f"Error encoding video '{media_file}':\n{e.stderr}")
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
                mkvmerge,
                '-o', output_file,
                temp_video_file,
                '--no-video', media_file
            ]
            # **Start Merging Process**
            try:
                subprocess.run(cmd_mkvmerge, check=True, text=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                print(f"Error merging files for {media_file}:\n{e.stderr}")
                continue

            os.remove(temp_video_file)
            os.remove(media_file)

            # **Delete Empty Media Directories**
            media_dir = os.path.dirname(media_file)
            delete_empty_media_dirs(media_dir, input_dir, media_extensions)

            remaining_files = wait_for_stable_files(input_dir)


if __name__ == "__main__":
    main()
