# YouTube Segment Download - Cookies Guide

YouTube often requires cookies to bypass bot detection. Here's how to use cookies with the download script:

## Quick Start

1. **Export cookies from your browser** (run this on your local machine where you have a browser):

   **Chrome/Edge:**
   ```bash
   yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://youtu.be/p_t6FEQm7z8?si=38kalSq8vQIpbel2"
   ```

   **Firefox:**
   ```bash
   yt-dlp --cookies-from-browser firefox --cookies cookies.txt "https://youtu.be/p_t6FEQm7z8?si=38kalSq8vQIpbel2"
   ```

2. **Transfer the cookies.txt file** to your workspace/server

3. **Run the download script with cookies:**
   ```bash
   python3 download_youtube_segment.py "https://youtu.be/p_t6FEQm7z8?si=38kalSq8vQIpbel2" 6:38 4:30 --cookies cookies.txt
   ```

## Alternative: Direct yt-dlp Command

You can also use yt-dlp directly:

```bash
yt-dlp --cookies cookies.txt \
  --format "best[ext=mp4]/best" \
  --download-sections "*398-668" \
  --no-playlist \
  --output "%(title)s_segment_6-38-11-08.%(ext)s" \
  "https://youtu.be/p_t6FEQm7z8?si=38kalSq8vQIpbel2"
```

## Notes

- Cookies expire, so you may need to re-export them periodically
- Make sure you're logged into YouTube in your browser when exporting cookies
- The cookies.txt file contains your authentication - keep it secure
