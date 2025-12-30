#!/bin/bash
# Download YouTube segment script

export PATH="$HOME/.local/bin:$PATH"

URL="https://youtu.be/p_t6FEQm7z8?si=38kalSq8vQIpbel2"
START_TIME="6:38"
DURATION="4:30"
COOKIES_FILE="cookies.txt"

# Convert start time to seconds (6:38 = 6*60 + 38 = 398)
START_SECONDS=398
END_SECONDS=668  # 11:08 = 11*60 + 8 = 668

echo "Attempting to download segment from $START_TIME to 11:08..."
echo "Using cookies file: $COOKIES_FILE"
echo ""

# Method 1: Try with download-sections and cookies
echo "Method 1: Using download-sections with cookies..."
yt-dlp \
  --cookies "$COOKIES_FILE" \
  --format "best[ext=mp4]/best" \
  --download-sections "*${START_SECONDS}-${END_SECONDS}" \
  --no-playlist \
  --output "%(title)s_segment_6-38-11-08.%(ext)s" \
  "$URL" 2>&1

if [ $? -eq 0 ]; then
  echo "✓ Success!"
  exit 0
fi

echo ""
echo "Method 1 failed. Trying Method 2..."

# Method 2: Try with web client
echo "Method 2: Using web player client..."
yt-dlp \
  --cookies "$COOKIES_FILE" \
  --format "best[ext=mp4]/best" \
  --download-sections "*${START_SECONDS}-${END_SECONDS}" \
  --no-playlist \
  --extractor-args "youtube:player_client=web" \
  --output "%(title)s_segment_6-38-11-08.%(ext)s" \
  "$URL" 2>&1

if [ $? -eq 0 ]; then
  echo "✓ Success!"
  exit 0
fi

echo ""
echo "Method 2 failed. Trying Method 3..."

# Method 3: Try without extractor args
echo "Method 3: Default settings..."
yt-dlp \
  --cookies "$COOKIES_FILE" \
  --format "best[ext=mp4]/best" \
  --download-sections "*${START_SECONDS}-${END_SECONDS}" \
  --no-playlist \
  --output "%(title)s_segment_6-38-11-08.%(ext)s" \
  "$URL" 2>&1

if [ $? -eq 0 ]; then
  echo "✓ Success!"
  exit 0
fi

echo ""
echo "All methods failed. The cookies may be expired or invalid."
echo "Please re-export cookies from your browser:"
echo "  yt-dlp --cookies-from-browser chrome --cookies cookies.txt \"$URL\""
exit 1
