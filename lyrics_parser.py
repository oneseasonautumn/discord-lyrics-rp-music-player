import re
from dataclasses import dataclass


@dataclass
class LyricLine:
    time_ms: int
    text: str


LRC_PATTERN = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)")


def parse_lrc(lrc_text: str) -> list[LyricLine]:
    """Parse LRC text into a sorted list of timed lyric lines."""
    lines: list[LyricLine] = []

    for raw in lrc_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue

        match = LRC_PATTERN.match(raw)
        if not match:
            continue

        minutes, seconds, fraction, text = match.groups()
        ms = int(minutes) * 60_000 + int(seconds) * 1_000

        if len(fraction) == 2:
            ms += int(fraction) * 10
        else:
            ms += int(fraction)

        text = text.strip()
        if text:
            lines.append(LyricLine(ms, text))

    lines.sort(key=lambda line: line.time_ms)
    return lines


def current_line(lines: list[LyricLine], position_ms: int) -> tuple[int, LyricLine | None]:
    """Return the index and lyric line active at the given playback position."""
    idx = -1

    for i, line in enumerate(lines):
        if line.time_ms <= position_ms:
            idx = i
        else:
            break

    if idx >= 0:
        return idx, lines[idx]

    return -1, None