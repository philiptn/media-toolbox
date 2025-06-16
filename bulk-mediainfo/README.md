# bulk-mediainfo
Two small utilities that display useful information in MKV files.  

`bulkmedia` - quickly display all video, audio and subtitle tracks in MKV files.  
`bulkmediav` - quickly identify video interlacing, resolution and first audio/subtitle language in MKV files.

#### Requirements
- Python >3.8
- mediainfo
- mkvtoolnix

If you are running Ubuntu/Debian Linux, you can just run `./apt-requirements.sh` to install the requirements.
## bulkmedia
```text
philip@PORTAL-PC:/mnt/e/media-toolbox/bulk-mediainfo$ python3 bulkmedia.py 

╭―――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――╮
│ TV Show - S01E01.mkv                                                │
│                                                                     │
│ id: 0, type: 'video', codec_name: 'HEVC/H.265/MPEG-H'               │
│ language: 'und', track_name: null, default_track: True              │
│ forced_track: False, codec_id: 'V_MPEGH/ISO/HEVC'                   │
│                                                                     │
│ id: 1, type: 'audio', codec_name: 'AC-3', language: 'nor'           │
│ track_name: null, default_track: False, forced_track: False         │
│ codec_id: 'A_AC3'                                                   │
│                                                                     │
│ id: 2, type: 'subtitles', codec_name: 'SubRip/SRT', language: 'nor' │
│ track_name: 'Norwegian', default_track: True, forced_track: False   │
│ codec_id: 'S_TEXT/UTF8'                                             │
│                                                                     │
│ id: 3, type: 'subtitles', codec_name: 'SubRip/SRT', language: 'eng' │
│ track_name: 'English', default_track: False, forced_track: False    │
│ codec_id: 'S_TEXT/UTF8'                                             │
╰―――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――╯
```
```cli
usage: bulkmedia.py [-h] [-r] [--debug] [folder]

Scan a folder and print MKV media info.

positional arguments:
  folder           Folder to scan

options:
  -h, --help       show this help message and exit
  -r, --recursive  Recursively scan subfolders
  --debug          Print debug information
```
## bulkmediav
```text
philip@PORTAL-PC:/mnt/e/media-toolbox/bulk-mediainfo$ python3 bulkmediav.py
                                                                                
Filesize   Codec             FPS     Interlace    Aspect  Resolution  Avg Bitrate  Max Bitrate  Audio  Subtitle  Filename
-------------------------------------------------------------------------------------------------------------------------
798.40 MB  V_MPEGH/ISO/HEVC  25.000  Progressive  48:23   1920x920    3.08 Mbps    N/A          no     no        TV Show - S01E01.mkv
```
```cli
usage: bulkmediav.py [-h] [-r] [--sort {filesize,codec,fps,interlace,aspect,resolution,avg_bitrate,max_bitrate,filename}] [folder]

Check video files in a folder and display metadata.

positional arguments:
  folder                Path to the folder containing video files, defaults to current folder.

options:
  -h, --help            show this help message and exit
  -r, --recursive       Recursively search subfolders for video files.
  --sort {filesize,codec,fps,interlace,aspect,resolution,avg_bitrate,max_bitrate,filename}
                        Column to sort the output by. Defaults to filename.
```