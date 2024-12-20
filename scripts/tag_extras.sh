#!/bin/bash

# Define keywords and their corresponding tags
declare -A tags
tags=(
  ["behindthescenes"]="-behindthescenes"
  ["deleted"]="-deleted"
  ["featurette"]="-featurette"
  ["interview"]="-interview"
  ["scene"]="-scene"
  ["short"]="-short"
  ["trailer"]="-trailer"
)

# Loop through all files in the current directory
for file in *.mkv; do
  # Skip if it's not a regular file
  [[ -e "$file" ]] || continue

  # Initialize a variable to store the matched tag
  matched_tag=""

  # Check for keywords in the filename (case-insensitive)
  for keyword in "${!tags[@]}"; do
    # Use grep for case-insensitive matching
    if echo "$file" | grep -iq "\b$keyword\b"; then
      matched_tag="${tags[$keyword]}"
      break
    fi
  done

  # If no keyword matched, use the "-other" tag
  if [[ -z "$matched_tag" ]]; then
    matched_tag="-other"
  fi

  # Append the tag to the filename (before the extension)
  base_name="${file%.*}"
  extension="${file##*.}"
  new_name="${base_name}${matched_tag}.${extension}"

  # Rename the file
  mv "$file" "$new_name"
  echo "Renamed: $file -> $new_name"
done

echo "Tagging complete!"

