#!/usr/bin/env python3
"""
Download a specific time segment from a YouTube video.

Usage:
    python download_youtube_segment.py <url> <start_time> <duration> [--cookies <cookies_file>]
    
Examples:
    # Download 4 minutes starting at 1:23:45
    python download_youtube_segment.py "https://youtube.com/watch?v=VIDEO_ID" 1:23:45 4:00
    
    # Download 4 minutes starting at 5025 seconds (1:23:45)
    python download_youtube_segment.py "https://youtube.com/watch?v=VIDEO_ID" 5025 240
    
    # Download from 1:23:45 to 1:27:45 (4 minutes)
    python download_youtube_segment.py "https://youtube.com/watch?v=VIDEO_ID" 1:23:45 1:27:45 --end-time
    
    # With cookies (to bypass YouTube bot detection)
    python download_youtube_segment.py "https://youtube.com/watch?v=VIDEO_ID" 1:23:45 4:00 --cookies cookies.txt
    
    # Export cookies from browser (Chrome/Edge):
    # yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://youtube.com/watch?v=VIDEO_ID"
    
    # Export cookies from browser (Firefox):
    # yt-dlp --cookies-from-browser firefox --cookies cookies.txt "https://youtube.com/watch?v=VIDEO_ID"
"""

import sys
import subprocess
import re
from pathlib import Path


def parse_time(time_str):
    """
    Parse time string in various formats:
    - HH:MM:SS (e.g., "1:23:45")
    - MM:SS (e.g., "23:45")
    - SS (e.g., "5025")
    Returns total seconds.
    """
    # Try to parse as seconds (pure number)
    if re.match(r'^\d+$', time_str):
        return int(time_str)
    
    # Parse as time format (HH:MM:SS or MM:SS)
    parts = time_str.split(':')
    if len(parts) == 3:
        hours, minutes, seconds = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds
    elif len(parts) == 2:
        minutes, seconds = map(int, parts)
        return minutes * 60 + seconds
    else:
        raise ValueError(f"Invalid time format: {time_str}")


def check_ytdlp_version():
    """Check yt-dlp version and return version info"""
    try:
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True, check=True)
        version_str = result.stdout.strip()
        # Parse version (e.g., "2024.1.1" or "2021.12.01")
        parts = version_str.split('.')
        if len(parts) >= 3:
            year = int(parts[0])
            month = int(parts[1])
            return (year, month)
        return None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def download_segment(url, start_time, duration=None, end_time=None, output_dir=None, cookies=None):
    """
    Download a specific segment from a YouTube video.
    
    Args:
        url: YouTube video URL
        start_time: Start time (seconds or HH:MM:SS format)
        duration: Duration in seconds (or HH:MM:SS format)
        end_time: End time in seconds (alternative to duration)
        output_dir: Output directory (default: current directory)
    """
    # Check if yt-dlp is installed
    version_info = check_ytdlp_version()
    if version_info is None:
        print("Error: yt-dlp is not installed.")
        print("Install it with: pip install yt-dlp")
        print("Or: pip install --upgrade yt-dlp")
        sys.exit(1)
    
    # Parse start time
    start_seconds = parse_time(start_time)
    
    # Calculate end time
    if end_time:
        end_seconds = parse_time(end_time)
        duration_seconds = end_seconds - start_seconds
    elif duration:
        duration_seconds = parse_time(duration)
        end_seconds = start_seconds + duration_seconds
    else:
        raise ValueError("Either duration or end_time must be provided")
    
    if duration_seconds <= 0:
        raise ValueError("Duration must be positive")
    
    # Build output filename
    start_time_str = format_time(start_seconds).replace(':', '-')
    end_time_str = format_time(end_seconds).replace(':', '-')
    output_template = f'%(title)s_segment_{start_time_str}-{end_time_str}.%(ext)s'
    if output_dir:
        output_template = str(Path(output_dir) / output_template)
    
    # Check if yt-dlp supports --download-sections (added in 2021.12.01)
    supports_sections = version_info >= (2021, 12)
    
    if supports_sections:
        # Method 1: Use --download-sections (most efficient - only downloads the segment)
        cmd = [
            'yt-dlp',
            '--format', 'best[ext=mp4]/best',
            '--download-sections', f'*{start_seconds}-{end_seconds}',
            '--no-playlist',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--extractor-args', 'youtube:player_client=android',
        ]
        if cookies:
            cmd.extend(['--cookies', cookies])
        cmd.extend(['--output', output_template, url])
        method = "download-sections (efficient)"
    else:
        # Method 2: Download full video and trim with ffmpeg (fallback for older versions)
        print("Note: Using fallback method (download + trim). Consider upgrading yt-dlp for better efficiency.")
        temp_output = str(Path(output_dir or '.') / 'temp_%(title)s.%(ext)s')
        cmd = [
            'yt-dlp',
            '--format', 'best[ext=mp4]/best',
            '--no-playlist',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--extractor-args', 'youtube:player_client=android',
        ]
        if cookies:
            cmd.extend(['--cookies', cookies])
        cmd.extend([
            '--output', temp_output,
            '--postprocessor-args', f'ffmpeg:-ss {start_seconds} -t {duration_seconds} -c copy',
            url
        ])
        method = "download + trim (fallback)"
    
    print(f"Downloading segment:")
    print(f"  URL: {url}")
    print(f"  Start: {start_seconds}s ({format_time(start_seconds)})")
    print(f"  Duration: {duration_seconds}s ({format_time(duration_seconds)})")
    print(f"  End: {end_seconds}s ({format_time(end_seconds)})")
    print(f"  Method: {method}")
    print(f"\nRunning: {' '.join(cmd)}\n")
    
    try:
        subprocess.run(cmd, check=True)
        print("\n✓ Download complete!")
        
        # Clean up temp file if using fallback method
        if not supports_sections:
            # Note: The temp file cleanup would need to be handled manually
            # or we'd need to track the actual filename, which is complex
            pass
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Error during download: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure yt-dlp is up to date: pip install --upgrade yt-dlp")
        print("2. Check that ffmpeg is installed (for fallback method)")
        print("3. Verify the URL and timestamps are correct")
        if not cookies:
            print("\n4. YouTube may require cookies to bypass bot detection.")
            print("   Export cookies from your browser:")
            print("   - Chrome/Edge: yt-dlp --cookies-from-browser chrome --cookies cookies.txt <url>")
            print("   - Firefox: yt-dlp --cookies-from-browser firefox --cookies cookies.txt <url>")
            print("   Then use: --cookies cookies.txt")
        sys.exit(1)


def format_time(seconds):
    """Format seconds as HH:MM:SS"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    
    url = sys.argv[1]
    start_time = sys.argv[2]
    
    # Check if using --end-time flag
    if '--end-time' in sys.argv:
        end_time_idx = sys.argv.index('--end-time')
        if end_time_idx + 1 < len(sys.argv):
            end_time = sys.argv[end_time_idx + 1]
            duration = None
        else:
            print("Error: --end-time requires an end time value")
            sys.exit(1)
    else:
        duration = sys.argv[3]
        end_time = None
    
    # Optional output directory
    output_dir = None
    if '--output-dir' in sys.argv:
        output_dir_idx = sys.argv.index('--output-dir')
        if output_dir_idx + 1 < len(sys.argv):
            output_dir = sys.argv[output_dir_idx + 1]
    
    # Optional cookies file
    cookies = None
    if '--cookies' in sys.argv:
        cookies_idx = sys.argv.index('--cookies')
        if cookies_idx + 1 < len(sys.argv):
            cookies = sys.argv[cookies_idx + 1]
        else:
            print("Error: --cookies requires a cookies file path")
            sys.exit(1)
    
    try:
        download_segment(url, start_time, duration=duration, end_time=end_time, output_dir=output_dir, cookies=cookies)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
