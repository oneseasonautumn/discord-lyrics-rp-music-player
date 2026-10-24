import os
import re
from dataclasses import dataclass, field

from mutagen import File as MutagenFile


AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".opus", ".aac"}
PLAYLIST_EXTS = {".m3u", ".m3u8", ".pls"}

# Matches [mm:ss.xx], [mm:ss.xxx], [m:ss.xx], [hh:mm:ss.xx]
LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{2,3})?\]")

ARTIST_KEYS = (
    "artist", "TPE1", "©art", "©ART", "Author", "ARTIST",
)
TITLE_KEYS = (
    "title", "TIT2", "©nam", "©NAM", "Title",
)
ALBUM_KEYS = (
    "album", "TALB", "©alb", "©ALB", "Album",
)


@dataclass
class Track:
    path: str
    title: str
    artist: str
    album: str
    has_lyrics: bool          # any embedded lyrics (synced or plain)
    has_timed_lyrics: bool    # lyrics contain LRC timestamps


@dataclass
class Playlist:
    name: str
    path: str | None
    tracks: list[str]


@dataclass
class Library:
    folder: str
    tracks: list[Track] = field(default_factory=list)
    playlists: list[Playlist] = field(default_factory=list)
    by_path: dict[str, Track] = field(default_factory=dict)
    by_artist: dict[str, list[Track]] = field(default_factory=dict)
    by_album: dict[str, list[Track]] = field(default_factory=dict)


# ------------------------------------------------------------ tag helpers

def read_tag(audio, keys: tuple[str, ...]) -> str:
    for key in keys:
        if key not in audio:
            continue
        value = audio[key]
        if isinstance(value, list):
            value = value[0] if value else ""
        else:
            value = str(value)
        value = value.replace("\x00", ", ").strip()
        if value:
            return value
    return ""


def has_timestamps(text: str | None) -> bool:
    """Return True if the text contains at least one LRC timestamp."""
    return bool(text and LRC_TIMESTAMP_RE.search(text))


def read_lyrics(audio) -> str | None:
    """Extract embedded lyrics. Prefers synced (timed) sources over plain text."""
    if audio is None:
        return None

    # --- FLAC / OGG Vorbis comments ---
    # Order matters: SYNCEDLYRICS first, then generic LYRICS, then plain.
    for key in ("SYNCEDLYRICS", "LYRICS", "UNSYNCHEDLYRICS", "lyrics"):
        if key in audio:
            value = audio[key]
            if isinstance(value, list):
                value = value[0] if value else ""
            else:
                value = str(value)
            if value.strip():
                return value

    # --- MP3 ID3 frames ---
    tags = getattr(audio, "tags", None)
    if tags is not None:
        # SYLT: synchronised lyrics (has timestamps) — try first.
        for key in tags.keys():
            if key.startswith("SYLT"):
                frame = tags[key]
                text = getattr(frame, "text", None)
                if text:
                    lines = []
                    for item in text:
                        if isinstance(item, tuple) and len(item) == 2:
                            lyric_text, timestamp_ms = item
                            minutes = timestamp_ms // 60000
                            seconds = (timestamp_ms % 60000) // 1000
                            centis = (timestamp_ms % 1000) // 10
                            lines.append(
                                f"[{minutes:02d}:{seconds:02d}.{centis:02d}]{lyric_text}"
                            )
                        else:
                            lines.append(str(item))
                    if lines:
                        return "\n".join(lines)

        # USLT: unsynchronised lyrics — plain text.
        for key in tags.keys():
            if key.startswith("USLT"):
                frame = tags[key]
                text = getattr(frame, "text", None)
                if text and str(text).strip():
                    return str(text)

        # TXXX:LYRICS fallback (some taggers put LRC here).
        for key in tags.keys():
            if key.startswith("TXXX") and "LYRICS" in key.upper():
                frame = tags[key]
                text = getattr(frame, "text", None)
                if text:
                    if isinstance(text, list):
                        text = text[0] if text else ""
                    if str(text).strip():
                        return str(text)

    # --- MP4 / M4A ---
    if "©lyr" in audio:
        value = audio["©lyr"]
        if isinstance(value, list):
            value = value[0] if value else ""
        else:
            value = str(value)
        if value.strip():
            return value

    return None


# ------------------------------------------------------------ playlist files

def _read_m3u(path: str) -> list[str]:
    tracks: list[str] = []
    base = os.path.dirname(path)
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                full = line if os.path.isabs(line) else os.path.join(base, line)
                if os.path.isfile(full):
                    tracks.append(os.path.normpath(full))
    except Exception as exc:
        print(f"Playlist read error ({path}): {exc}")
    return tracks


def _read_pls(path: str) -> list[str]:
    tracks: list[str] = []
    base = os.path.dirname(path)
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("["):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if not key.lower().startswith("file"):
                    continue
                full = value if os.path.isabs(value) else os.path.join(base, value)
                if os.path.isfile(full):
                    tracks.append(os.path.normpath(full))
    except Exception as exc:
        print(f"PLS read error ({path}): {exc}")
    return tracks


def _read_playlist_file(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".m3u", ".m3u8"):
        return _read_m3u(path)
    if ext == ".pls":
        return _read_pls(path)
    return []


# ------------------------------------------------------------ scan

def _read_track(path: str) -> Track:
    title = os.path.splitext(os.path.basename(path))[0]
    artist = "Unknown Artist"
    album = ""
    has_lyrics = False
    has_timed_lyrics = False

    try:
        audio = MutagenFile(path)
        if audio is not None:
            t = read_tag(audio, TITLE_KEYS)
            if t:
                title = t
            a = read_tag(audio, ARTIST_KEYS)
            if a:
                artist = a
            album = read_tag(audio, ALBUM_KEYS)

            lyrics_text = read_lyrics(audio)
            has_lyrics = bool(lyrics_text and lyrics_text.strip())
            has_timed_lyrics = has_lyrics and has_timestamps(lyrics_text)
    except Exception as exc:
        print(f"Track read error ({path}): {exc}")

    return Track(
        path=path,
        title=title,
        artist=artist,
        album=album,
        has_lyrics=has_lyrics,
        has_timed_lyrics=has_timed_lyrics,
    )


def scan_folder(folder: str, progress=None) -> Library:
    lib = Library(folder=folder)

    try:
        names = sorted(os.listdir(folder))
    except Exception as exc:
        print(f"Folder read error ({folder}): {exc}")
        return lib

    files = [n for n in names if os.path.isfile(os.path.join(folder, n))]
    total = len(files)

    for i, name in enumerate(files):
        full = os.path.normpath(os.path.join(folder, name))
        ext = os.path.splitext(name)[1].lower()

        if progress:
            progress(i + 1, total, name)

        if ext in AUDIO_EXTS:
            track = _read_track(full)
            lib.tracks.append(track)
            lib.by_path[track.path] = track
            lib.by_artist.setdefault(track.artist, []).append(track)
            lib.by_album.setdefault(track.album or "Unknown Album", []).append(track)

        elif ext in PLAYLIST_EXTS:
            tracks = _read_playlist_file(full)
            if tracks:
                lib.playlists.append(
                    Playlist(
                        name=os.path.splitext(name)[0],
                        path=full,
                        tracks=tracks,
                    )
                )

    # Auto playlist: every track whose lyrics contain LRC timestamps.
    timed_tracks = [t.path for t in lib.tracks if t.has_timed_lyrics]
    auto = Playlist(
        name="All With Timed Lyrics",
        path=None,
        tracks=timed_tracks,
    )
    lib.playlists.insert(0, auto)

    # Stable sort indexes
    for lst in lib.by_artist.values():
        lst.sort(key=lambda t: (t.album or "", t.title.lower()))
    for lst in lib.by_album.values():
        lst.sort(key=lambda t: (t.artist.lower(), t.title.lower()))

    return lib