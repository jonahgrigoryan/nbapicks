#!/usr/bin/env zsh
# Zsh-compatible YouTube segment download script (macOS version)

# Add common yt-dlp installation paths for macOS
export PATH="$HOME/.local/bin:$PATH"
export PATH="/usr/local/bin:$PATH"
export PATH="/opt/homebrew/bin:$PATH"

# Video details
URL="https://youtu.be/p_t6FEQm7z8?si=38kalSq8vQIpbel2"
START_SECONDS=398  # 6:38
END_SECONDS=668    # 11:08
COOKIES_FILE="cookies.txt"

echo "Downloading YouTube segment (6:38 to 11:08)..."
echo ""

# Run the download command
yt-dlp \
  --cookies "$COOKIES_FILE" \
  --format "best[ext=mp4]/best" \
  --download-sections "*${START_SECONDS}-${END_SECONDS}" \
  --no-playlist \
  --output "%(title)s_segment_6-38-11-08.%(ext)s" \
  "$URL"

if [[ $? -eq 0 ]]; then
  echo ""
  echo "✓ Download complete!"
else
  echo ""
  echo "✗ Download failed. The cookies may be expired."
  echo ""
  echo "To refresh cookies, run this on your local machine (with browser):"
  echo "  yt-dlp --cookies-from-browser chrome --cookies cookies.txt \"$URL\""
  echo ""
  echo "Then upload the new cookies.txt file to this workspace."
fi
