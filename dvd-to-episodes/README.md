# dvd-to-episodes
A tool that automatically splits and stitches chapters of DVD video files into episodes.

### How to use
#### Linux (Ubuntu/Debian)
1. Install required packages with `./install_prerequisites.sh`
2. Place video files (or folder) in current directory.
3. Run `python3 dvd-to-episodes.py`

#### Windows
All required packages and binaries will be downloaded automatically.

1. Place video files (or folder) in current directory.
2. Run `dvd-to-episodes.bat`

### Example run
```text
Select a folder to process:
1. Current
2. Custom

Select an option (1-2): 1

Enter the TV show name: TV Show

Enter the output folder: TV Show

╭------------------------------------------------------------╮
| 1. 01.mkv (Size: 6.32 GB, Duration: 2:09:24, Chapters: 27) |
╰------------------------------------------------------------╯

Choose a file number to process (0 to exit): 1

What season number is this file associated with?: 1

Which episode number does this file start at?: 1

Splitting chapters for 01.mkv... Done.

How many episodes are in this file?: 13

Which chapter does episode 1 start at?: 1
Which chapter does episode 2 start at?: 3
Which chapter does episode 3 start at?: 5
Which chapter does episode 4 start at?: 7
Which chapter does episode 5 start at?: 9
Which chapter does episode 6 start at?: 11
Which chapter does episode 7 start at?: 13
Which chapter does episode 8 start at?: 15
Which chapter does episode 9 start at?: 17
Which chapter does episode 10 start at?: 19
Which chapter does episode 11 start at?: 21
Which chapter does episode 12 start at?: 23
Which chapter does episode 13 start at?: 25

Merging chapters to create 'TV Show - S01E01.mkv'... Done.
Merging chapters to create 'TV Show - S01E02.mkv'... Done.
Merging chapters to create 'TV Show - S01E03.mkv'... Done.
Merging chapters to create 'TV Show - S01E04.mkv'... Done.
Merging chapters to create 'TV Show - S01E05.mkv'... Done.
Merging chapters to create 'TV Show - S01E06.mkv'... Done.
Merging chapters to create 'TV Show - S01E07.mkv'... Done.
Merging chapters to create 'TV Show - S01E08.mkv'... Done.
Merging chapters to create 'TV Show - S01E09.mkv'... Done.
Merging chapters to create 'TV Show - S01E10.mkv'... Done.
Merging chapters to create 'TV Show - S01E11.mkv'... Done.
Merging chapters to create 'TV Show - S01E12.mkv'... Done.
Merging chapters to create 'TV Show - S01E13.mkv'... Done.

╭-------------------------------------------------------------------╮
| 1. (DONE) 01.mkv (Size: 6.32 GB, Duration: 2:09:24, Chapters: 27) |
╰-------------------------------------------------------------------╯

Choose a file number to process (0 to exit): 0
```