#!/usr/bin/env bash

set -euo pipefail

usage() {
    echo "Usage: $(basename "$0") [options] <input_file>"
    echo ""
    echo "Arguments:"
    echo "  input_file        Input video file"
    echo ""
    echo "Options:"
    echo "  --crf <value>     CRF value for libx264 (default: 20)"
    echo "  -h, --help        Show this help message"
    exit "${1:-0}"
}

# Parse arguments
if [[ $# -eq 0 ]]; then
    echo "Error: No input file specified."
    usage 1
fi

INPUT=""
CRF=20

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage 0
            ;;
        --crf)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --crf requires a value."
                usage 1
            fi
            CRF="$2"
            shift 2
            ;;
        -*)
            echo "Error: Unknown option '$1'"
            usage 1
            ;;
        *)
            if [[ -n "$INPUT" ]]; then
                echo "Error: Multiple input files specified."
                usage 1
            fi
            INPUT="$1"
            shift
            ;;
    esac
done

if [[ -z "$INPUT" ]]; then
    echo "Error: No input file specified."
    usage 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: File not found: '$INPUT'"
    exit 1
fi

# Derive output filename: same name, .mp4 extension
BASENAME="${INPUT%.*}"
OUTPUT="${BASENAME}.mp4"

echo "Input:  $INPUT"
echo "Output: $OUTPUT"
echo "CRF:    $CRF"

ffmpeg -i "$INPUT" \
    -c:a aac -aq 5 -ac 2 \
    -c:v libx264 -crf "$CRF" -preset slow -tune grain \
    -bf 4 -rc-lookahead 32 -aq-mode 3 -b-pyramid normal -coder 1 \
    -sn \
    "$OUTPUT"
