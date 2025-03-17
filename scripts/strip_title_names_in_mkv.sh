#!/bin/bash

# If no argument is provided, use the current directory; otherwise use the provided argument.
if [ -z "$1" ]; then
    INPUT_DIR="."
else
    INPUT_DIR="$1"
fi

# Ensure mkvpropedit and mkvmerge are installed
if ! command -v mkvpropedit &> /dev/null || ! command -v mkvmerge &> /dev/null; then
    echo "Error: mkvpropedit or mkvmerge is not installed or not in PATH."
    exit 1
fi

# Process all MKV files recursively
find "$INPUT_DIR" -type f -name "*.mkv" | sort | while IFS= read -r FILE; do
    # Extract numeric track IDs only and increment by 1
    TRACK_IDS=$(mkvmerge -i "$FILE" | grep -oP 'Track ID \d+' | awk '{print $3}')

    # Remove track names for all tracks
    for TRACK_ID in $TRACK_IDS; do
        NEW_TRACK_ID=$((TRACK_ID + 1))  # Increment track ID
        mkvpropedit "$FILE" --edit track:$NEW_TRACK_ID --set name="" > /dev/null 2>&1
    done

    # Remove title from the MKV file
    mkvpropedit "$FILE" --edit info --set title="" > /dev/null 2>&1

    echo "Stripped track names and title from: '$(basename "$FILE")'"
done
