#!/usr/bin/env python3
"""
Convert ElevenLabs Speech-to-Text JSON export to SRT subtitle format.

Usage:
    python json_to_srt.py
"""

import json
from pathlib import Path

# =============================================================================
# CONFIGURATION — Adjust these as needed
# =============================================================================

INPUT_JSON = Path("SPEAK THE WORD.mp4.json")
OUTPUT_SRT = Path("SPEAK THE WORD.srt")

# Subtitle grouping parameters
MAX_DURATION_SECONDS = 4.5      # Maximum on-screen time per subtitle
MAX_CHARS = 60                  # Maximum characters per subtitle line
MIN_CHARS_FOR_PUNCT_BREAK = 20  # Min chars before allowing punctuation break

# Punctuation that can end a subtitle early (once min chars reached)
BREAK_PUNCTUATION = {'.', '?', '!', ',', ';', ':'}


# =============================================================================
# FUNCTIONS
# =============================================================================

def fmt_time(seconds: float) -> str:
    """
    Convert float seconds to SRT timestamp format: HH:MM:SS,mmm

    Examples:
        0.0      -> "00:00:00,000"
        3.5      -> "00:00:03,500"
        2672.656 -> "00:44:32,656"
    """
    if seconds < 0:
        seconds = 0.0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))

    # Handle edge case where rounding gives 1000ms
    if millis >= 1000:
        millis = 999

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def load_words(json_path: Path) -> list[dict]:
    """
    Load and flatten all words from ElevenLabs JSON.

    Returns a list of dicts: [{"text": str, "start": float, "end": float}, ...]
    Skips any whitespace-only word entries.
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    words = []
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            text = word.get("text", "")
            # Skip whitespace-only entries
            if not text.strip():
                continue
            words.append({
                "text": text.strip(),
                "start": word.get("start_time", 0.0),
                "end": word.get("end_time", 0.0),
            })

    return words


def build_subtitles(words: list[dict]) -> list[dict]:
    """
    Group words into subtitle chunks.

    Heuristics:
    - Max duration: MAX_DURATION_SECONDS
    - Max characters: MAX_CHARS
    - Prefer breaking at punctuation once MIN_CHARS_FOR_PUNCT_BREAK reached

    Returns list of dicts: [{"start": float, "end": float, "text": str}, ...]
    """
    if not words:
        return []

    subtitles = []

    # Current subtitle being built
    current_words = []
    current_text = ""
    current_start = None
    current_end = None

    def finalize_subtitle():
        """Save current subtitle and reset state."""
        nonlocal current_words, current_text, current_start, current_end
        if current_words:
            subtitles.append({
                "start": current_start,
                "end": current_end,
                "text": current_text,
            })
        current_words = []
        current_text = ""
        current_start = None
        current_end = None

    def join_word(existing: str, new_word: str) -> str:
        """
        Join a new word to existing text with proper spacing.
        No space before punctuation, space between normal words.
        """
        if not existing:
            return new_word

        # Check if new word starts with punctuation
        if new_word and new_word[0] in BREAK_PUNCTUATION:
            return existing + new_word

        return existing + " " + new_word

    for word in words:
        word_text = word["text"]
        word_start = word["start"]
        word_end = word["end"]

        # Calculate what text would look like with this word added
        potential_text = join_word(current_text, word_text)
        if current_start is not None:
            potential_duration = word_end - current_start
        else:
            potential_duration = 0.0

        # Check if we need to start a new subtitle
        should_break = False

        if current_words:
            # Would exceed max duration?
            if potential_duration > MAX_DURATION_SECONDS:
                should_break = True
            # Would exceed max characters?
            elif len(potential_text) > MAX_CHARS:
                should_break = True

        if should_break:
            finalize_subtitle()

        # Add word to current subtitle
        if current_start is None:
            current_start = word_start

        current_text = join_word(current_text, word_text)
        current_end = word_end
        current_words.append(word)

        # Check if we should break at punctuation
        if (len(current_text) >= MIN_CHARS_FOR_PUNCT_BREAK
                and word_text and word_text[-1] in BREAK_PUNCTUATION):
            finalize_subtitle()

    # Don't forget the last subtitle
    finalize_subtitle()

    return subtitles


def write_srt(subtitles: list[dict], srt_path: Path) -> None:
    """
    Write subtitles to SRT file format.

    SRT format:
        1
        00:00:00,000 --> 00:00:03,500
        Subtitle text here

        2
        00:00:03,600 --> 00:00:07,000
        Next subtitle text

    """
    lines = []
    for idx, sub in enumerate(subtitles, start=1):
        lines.append(str(idx))
        lines.append(f"{fmt_time(sub['start'])} --> {fmt_time(sub['end'])}")
        lines.append(sub['text'])
        lines.append("")  # Blank line between blocks

    with open(srt_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def main():
    """Main entry point."""
    print(f"Input JSON:  {INPUT_JSON.resolve()}")
    print(f"Output SRT:  {OUTPUT_SRT.resolve()}")
    print()

    # Check input exists
    if not INPUT_JSON.exists():
        print(f"ERROR: Input file not found: {INPUT_JSON}")
        return

    # Load words
    print("Loading words from JSON...")
    words = load_words(INPUT_JSON)
    print(f"  → Loaded {len(words)} words (whitespace tokens excluded)")

    if not words:
        print("WARNING: No words found in JSON. Nothing to convert.")
        return

    # Show first and last word for verification
    print(f"  → First word: \"{words[0]['text']}\" @ {words[0]['start']:.3f}s")
    last = words[-1]
    print(f"  → Last word:  \"{last['text']}\" @ {last['start']:.3f}s"
          f" - {last['end']:.3f}s")
    print()

    # Build subtitles
    print("Building subtitle chunks...")
    subtitles = build_subtitles(words)
    print(f"  → Generated {len(subtitles)} subtitle entries")

    if subtitles:
        total_duration = subtitles[-1]['end']
        avg_duration = sum(s['end'] - s['start'] for s in subtitles)
        avg_duration /= len(subtitles)
        print(f"  → Total duration: {fmt_time(total_duration)}")
        print(f"  → Average subtitle duration: {avg_duration:.2f}s")
    print()

    # Write SRT
    print("Writing SRT file...")
    write_srt(subtitles, OUTPUT_SRT)
    print(f"  → Done! Saved to: {OUTPUT_SRT.resolve()}")


if __name__ == "__main__":
    main()
