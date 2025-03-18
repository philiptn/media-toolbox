# media-encoder
Uses ffmpeg and mkvmerge to encode and repack the media. Includes optimized encoding parameters, as well as suggested CRF settings for each codec/tuning. Also supports cropping and resizing.
#### How to use:
Note: If you are using Windows Command Prompt, replace all `python3` and `pip3` commands with `python` and `pip`. 
1. Navigate to the media-encoder folder: `cd media-encoder`
2. Put media files inside `input/`.
3. Make sure that you have `ffmpeg` and `mkvmerge` installed. If not, run `prerequisites.sh` to install dependencies (Ubuntu/Debian). If you are running Windows, make sure that `ffmpeg` , `ffrobe` and `mkvmerge` are available in `PATH`.
4. Create a virtual environment using `python3 -m venv venv` and run `venv/bin/activate` to activate it.  
   If you are using Windows, create the environment with `python -m venv venv_win` and activate it with `venv_win\Scripts\activate`. 
5. Install the required pip packages using `pip3 install -r requirements`.
6. Run the program:`python3 media-encoder.py`
7. Encoded media will be saved to the `output/` folder. These can also be previewed using VLC media player while they are being encoded.

#### Example usage:
````text
Do you want to remove any black bars in the video stream? (yes/no): yes

Enter crop values (left,right,top,bottom): auto

Do you want to resize the video stream to a specific aspect ratio? (yes/no): no

Enter output codec (e.g., 'h264', 'h265', 'vp9', 'av1'): h265

CRF 18 - Effectively transparent from source in most cases
CRF 20 - More space saving, with minimal loss to some high-level details
Enter quality setting (CRF): 18

CRF is set to 18. No tune needed (even with grainy source material)
Available tune options for h265: grain, fastdecode, zerolatency, psnr, ssim, animation
Enter tune setting (optional):

No tune has been applied. Using speed 'medium' is recommended.
Available speed options for h265: slow, medium
Enter encoder speed: medium

Enter the maximum CPU usage percentage (e.g., '50' for 50%): auto

Do you want to add custom FFmpeg parameters? (yes/no): no

Select preferred FFmpeg UI (compact, advanced): compact

⠇ Processing TV.Show.S01E01.mkv ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   1% 0:00:14 31:55
````

### Acknowledgments

#### This project would not be possible without the following third-party tools/packages: 

FFmpeg  
https://ffmpeg.org/

MKVToolNix (for managing MKV files, extracting, merging, file info, etc.)  
https://mkvtoolnix.download/

prompt-toolkit (for prefilling the input prompts cross-platform)  
https://github.com/prompt-toolkit/python-prompt-toolkit

CrypticSignal for better-ffmpeg-progress (displaying the ffmpeg process as a nice progress bar)  
https://github.com/CrypticSignal/better-ffmpeg-progress

rich (for handling colors and formatting)  
https://github.com/Textualize/rich