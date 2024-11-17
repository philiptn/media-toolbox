# media-encoder
A custom media encoder script based on Python. Uses ffmpeg and mkvmerge to encode and repack the media. Includes some custom encoding parameters, as well as suggested CRF settings for each scenario. Will also ask the user for cropping and aspect ratio.

#### How to use:
1. Navigate to the media-encoder folder: `cd media-encoder`
2. Put media files inside `input/`.
3. Make sure that you have `ffmpeg` and `mkvmerge` installed. If not, run `prerequisites.sh` to install dependencies (Ubuntu/Debian). If you are running Windows, make sure that `ffmpeg` , `ffrobe` and `mkvmerge` are available in `PATH`.
4. Run the program:`python3 media-encoder.py`
5. Encoded media will be saved to the `output/` folder. These can also be previewed using VLC media player while they are being encoded.

#### Example usage
````text
W:\home\philip\media-toolbox\media-encoder>python3 media-encoder.py

Enter crop values (left, right, top, bottom), e.g., '0,0,104,104': 0,0,0,0

Enter output aspect ratio, e.g., '16:9': 16:9

Enter codec (e.g., 'h264', 'h265', 'vp9', 'av1'): h265

Recommended values (1080p):
H.264 AVC Standard (no tune)   -  CRF 20
H.264 AVC Grain                -  CRF 22
H.265 HEVC Standard (no tune)  -  CRF 20
H.265 HEVC Grain               -  CRF 22

Enter quality setting (CRF): 22

Available tune options for h265: grain, fastdecode, zerolatency, psnr, ssim
Enter tune setting (optional): grain

Enter the maximum CPU usage percentage (e.g., '50' for 50%): 80

Using 2 encoder thread(s) based on CPU usage percentage.
Processing file: input\media.mkv
Encoding video input\media.mkv...
...
````