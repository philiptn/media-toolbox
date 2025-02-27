import os
import json
import subprocess
import shutil
import math
import datetime
import platform
from prompt_toolkit import prompt


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


def list_folders(directory):
    folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f)) and not f.startswith('.')]
    for i, folder in enumerate(folders, 1):
        print(f"{i}. {folder}")
    print(f"{len(folders) + 1}. Current")
    print(f"{len(folders) + 2}. Custom\n")
    return folders


def convert_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


def get_file_duration(filename):
    result = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                             "format=duration", "-of",
                             "default=noprint_wrappers=1:nokey=1", filename],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    duration = float(result.stdout)
    return str(datetime.timedelta(seconds=int(duration)))


def get_number_of_chapters(filename):
    command = [ffprobe, "-print_format", "json", "-show_chapters", "-loglevel", "error", filename]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
    try:
        chapters_info = json.loads(result.stdout)
        return len(chapters_info.get("chapters", []))
    except json.JSONDecodeError:
        return 0


def list_mkvs(directory, processed_files):
    mkvs = [f for f in os.listdir(directory) if f.endswith('.mkv')]
    lines = []
    max_line_length = 0

    for i, file in enumerate(mkvs, 1):
        full_path = os.path.join(directory, file)
        size = convert_size(os.path.getsize(full_path))
        duration = get_file_duration(full_path)
        chapters = get_number_of_chapters(full_path)
        processed_status = "(DONE) " if file in processed_files else ""
        line = f"{i}. {processed_status}{file} (Size: {size}, Duration: {duration}, Chapters: {chapters})"
        lines.append(line)
        max_line_length = max(max_line_length, len(line))

    border_line_top = "╭" + "-" * (max_line_length + 2) + "╮"
    border_line_bottom = "╰" + "-" * (max_line_length + 2) + "╯"

    print('')
    print(border_line_top)
    for line in lines:
        print(f"| {line.ljust(max_line_length)} |")
    print(border_line_bottom)

    return mkvs


def split_chapters(input_filename, temp_directory):
    print(f"\nSplitting chapters for {os.path.basename(input_filename)}... ", end='')
    command = f"{mkvmerge} --split chapters:all \"{input_filename}\" -o \"{os.path.join(temp_directory, '%02d.mkv')}\""
    subprocess.run(command, shell=True, encoding='utf-8', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Done.")


def get_split_chapter_files(temp_directory):
    chapter_files = []
    for file in os.listdir(temp_directory):
        if file.endswith('.mkv'):
            chapter_files.append(file)
    chapter_files.sort()
    return chapter_files


def merge_chapters(chapter_files, output_file, output_directory, temp_directory):
    print(f"Merging chapters to create '{output_file}'... ", end='')
    input_files = ' + '.join(f'"{os.path.join(temp_directory, file)}"' for file in chapter_files)
    command = f"{mkvmerge} -o \"{os.path.join(output_directory, output_file)}\" {input_files}"
    subprocess.run(command, shell=True, encoding='utf-8', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Done.")


def clean_temp_directory(temp_directory):
    if os.path.exists(temp_directory):
        shutil.rmtree(temp_directory)


def process_file(input_directory, output_directory, filename, tv_show_title, episode_counts, season, starting_episode):
    input_full_path = os.path.join(input_directory, filename)
    temp_directory = os.path.join(output_directory, ".tmp")

    if not os.path.exists(temp_directory):
        os.makedirs(temp_directory)

    split_chapters(input_full_path, temp_directory)
    chapter_files = get_split_chapter_files(temp_directory)

    print('')
    while True:
       try:
          num_episodes = input(f"How many episodes are in this file?: ")
          num_episodes = int(num_episodes)
          break
       except ValueError:
          pass
    episode_mappings = {}
    print('')
    for ep in range(starting_episode, starting_episode + num_episodes):
        while True:
           try:
              start_chapter = input(f"Which chapter does episode {ep} start at?: ")
              start_chapter = int(start_chapter)
              break
           except ValueError:
              pass
        episode_mappings[ep] = start_chapter

    print('')
    for ep in range(starting_episode, starting_episode + num_episodes):
        start_chapter = episode_mappings[ep]
        end_chapter = episode_mappings.get(ep + 1, len(chapter_files) + 1) - 1
        if ep == starting_episode + num_episodes - 1:  # For the last episode in this file
            episode_files = chapter_files[start_chapter - 1:]
        else:
            episode_files = chapter_files[start_chapter - 1:end_chapter]
        episode_name = f"{tv_show_title} - S{season}E{ep:02d}.mkv"
        merge_chapters(episode_files, episode_name, output_directory, temp_directory)

    # Clear temp files
    clean_temp_directory(temp_directory)

    # Update episode count for the season
    episode_counts[season] = starting_episode + num_episodes
    return episode_counts


if __name__ == "__main__":
    episode_counts = {}
    tv_show_title = ''

    input_directory = ''
    while not input_directory:
        print("Select a folder to process:")
        current_directory = os.getcwd()
        folders = list_folders(current_directory)

        folder_choice = ''
        while True:
            try:
                folder_choice = prompt(f"Select an option (1-{len(folders) + 2}): ")
                folder_choice = int(folder_choice)
                break
            except ValueError:
                pass

        if folder_choice == len(folders) + 1:
            input_directory = "."
        elif folder_choice == len(folders) + 2:
            input_directory = input("\nCustom input folder path: ")
        else:
            input_directory = os.path.join(current_directory, folders[folder_choice - 1])

    while not tv_show_title:
        tv_show_title = input("\nEnter the TV show name: ")

    output_directory = prompt("\nEnter the output folder: ", default=tv_show_title)

    if output_directory == tv_show_title:
        output_directory = os.path.join(current_directory, tv_show_title)

    processed_files = []
    temp_directory = os.path.join(output_directory, ".tmp")

    while True:
        mkvs = list_mkvs(input_directory, processed_files)
        if not mkvs:
            print("All files have been processed.")
            break

        file_choice = ''
        print('')
        while True:
           try:
              file_choice = input("Choose a file number to process (0 to exit): ")
              file_choice = int(file_choice)
              break
           except ValueError:
              pass
        if file_choice == 0:
            clean_temp_directory(temp_directory)
            print()
            break

        selected_file = mkvs[file_choice - 1]
        season = prompt("\nWhat season number is this file associated with?: ", default="1")
        season = season.zfill(2) if season else "01"

        # Ask for starting episode number
        starting_episode = ''
        while True:
            try:
                starting_episode = prompt(f"\nWhich episode number does this file start at?: ", default="1")
                starting_episode = starting_episode.zfill(2) if starting_episode else "01"
                starting_episode = int(starting_episode)
                break
            except ValueError:
                pass

        episode_counts = process_file(input_directory, output_directory, selected_file, tv_show_title, episode_counts, season, starting_episode)
        processed_files.append(selected_file)
