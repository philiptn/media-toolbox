#!/bin/bash

# Update package lists
echo "Updating package lists..."
sudo apt-get update

# Install MKVToolNix
echo "Installing MKVToolNix..."
sudo apt-get install -y mkvtoolnix

# Install FFmpeg
echo "Installing FFmpeg..."
sudo apt-get install -y ffmpeg

echo "Installation completed."

