#!/bin/bash

# Check if mediainfo is installed
if ! command -v mediainfo &> /dev/null; then
    echo "mediainfo is required but not installed. Please install it first."
    exit 1
fi

# Check if an argument (input path) is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <directory>"
    exit 1
fi

# Set the base directory
BASE_DIR="$1"

# Function to check if an MKV file has a variable frame rate
check_vfr() {
    local file="$1"
    
    # Extract frame rate mode using mediainfo
    frame_mode=$(mediainfo --Output="Video;%FrameRate_Mode%" "$file")

    # If the frame rate mode is "Variable", print the file path and its frame rate
    if [[ "$frame_mode" == "VFR" ]]; then
        frame_rate=$(mediainfo --Output="Video;%FrameRate%" "$file")
        printf "Variable Frame Rate detected: %s\nFrame Rate: %.3f fps\n\n" "$file" "$frame_rate"
    fi
}

# Export function for use in find command
export -f check_vfr

# Find and check all MKV files recursively in the given directory
find "$BASE_DIR" -type f -name "*.mkv" -exec bash -c 'check_vfr "$0"' {} \;

