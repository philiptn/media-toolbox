# media-encoder
A custom media encoder script based on Python. Uses ffmpeg and mkvmerge to encode and repack the media. Includes optimized encoding parameters, as well as suggested CRF settings for each codec/tuning. Also supports cropping, resizing and denoising (h265).
#### How to use:
1. Navigate to the media-encoder folder: `cd media-encoder`
2. Put media files inside `input/`.
3. Make sure that you have `ffmpeg` and `mkvmerge` installed. If not, run `prerequisites.sh` to install dependencies (Ubuntu/Debian). If you are running Windows, make sure that `ffmpeg` , `ffrobe` and `mkvmerge` are available in `PATH`.
4. Create a virtual environment using `python3 -m venv venv` and run `venv/bin/activate` to activate it.
5. Install the required pip packages using `pip3 install -r requirements`.
6. Run the program:`python3 media-encoder.py`
7. Encoded media will be saved to the `output/` folder. These can also be previewed using VLC media player while they are being encoded.

#### Example usage
````text
(venv_win) W:\home\philip\media-toolbox\media-encoder>python media-encoder.py

Do you want to crop the video stream? (yes/no): no

Do you want to resize the video stream to a specific aspect ratio? (yes/no): no

Enter output codec (e.g., 'h264', 'h265', 'vp9', 'av1'): h265

Do you want to enable denoising? (yes/no): no

Recommended values (1080p):
H.264 AVC Standard                          -  CRF 20
H.264 AVC Grain                             -  CRF 22
H.265 HEVC Standard                         -  CRF 20
H.265 HEVC Grain (Recommended)              -  CRF 24
H.265 HEVC Denoised                         -  CRF 22

Enter quality setting (CRF): 24

Available tune options for h265: grain, fastdecode, zerolatency, psnr, ssim, animation
Enter tune setting (optional): grain

Enter the maximum CPU usage percentage (e.g., '50' for 50%): 85

Using 6 encoder thread(s) based on CPU usage percentage.

Processing file: input\media.mkv
Encoding video input\media.mkv...
...
````