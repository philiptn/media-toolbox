#!/bin/bash

for file in *.mp3; do
  base="${file%.mp3}"

  # Extract embedded album art (decode for max resolution)
  ffmpeg -y -i "$file" -an -vframes 1 "${base}_cover.jpg"

  if [ ! -f "${base}_cover.jpg" ]; then
    echo "No embedded cover found in $file, skipping."
    continue
  fi

  # Encode to MKV with proper colors and full compatibility
  ffmpeg -y -loop 1 -i "${base}_cover.jpg" -i "$file" \
    -vf "scale=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p" \
    -c:v libx265 -crf 18 \
    -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
    -c:a copy -shortest "${base}.mkv"

  rm "${base}_cover.jpg"

  echo "Created ${base}.mkv"
done

