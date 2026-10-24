import hashlib
import io
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import requests
from mutagen import File as MutagenFile

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    Image = None
    _PIL_AVAILABLE = False


CACHE_DIR = Path(tempfile.gettempdir()) / "lyric-player-art-cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_DIMENSION = 1024
MAX_BYTES = 500 * 1024
JPEG_QUALITY = 85


# ------------------------------------------------------------ in-memory cover cache

_COVER_CACHE_LOCK = threading.Lock()
_COVER_CACHE: OrderedDict[str, tuple[bytes, str] | None] = OrderedDict()
_COVER_CACHE_MAX = 16


def _cache_lookup(path: str):
    """Return cached value (or None if cached-as-missing), or False if not cached."""
    with _COVER_CACHE_LOCK:
        if path in _COVER_CACHE:
            _COVER_CACHE.move_to_end(path)
            return _COVER_CACHE[path]
    return False


def _cache_store(path: str, value: tuple[bytes, str] | None) -> None:
    with _COVER_CACHE_LOCK:
        _COVER_CACHE[path] = value
        _COVER_CACHE.move_to_end(path)
        while len(_COVER_CACHE) > _COVER_CACHE_MAX:
            _COVER_CACHE.popitem(last=False)


def cache_cover(path: str, value: tuple[bytes, str] | None) -> None:
    """Public: seed the cache from an already-extracted cover."""
    _cache_store(path, value)


# ------------------------------------------------------------ extraction

def extract_cover_from_audio(audio) -> tuple[bytes, str] | None:
    """Extract cover bytes from an already-parsed MutagenFile object.

    No file I/O — use this when you already have the audio object.
    """
    if audio is None:
        return None

    # MP3 / ID3
    if hasattr(audio, "tags") and audio.tags:
        for key in audio.tags.keys():
            if key.startswith("APIC"):
                apic = audio.tags[key]
                return apic.data, apic.mime or "image/jpeg"

    # FLAC / OGG
    if hasattr(audio, "pictures") and audio.pictures:
        pic = audio.pictures[0]
        return pic.data, pic.mime or "image/jpeg"

    # MP4 / M4A
    if "covr" in audio:
        covers = audio["covr"]
        if covers:
            data = bytes(covers[0])
            return data, "image/jpeg"

    return None


def _extract_cover_bytes(path: str) -> tuple[bytes, str] | None:
    """Open the file and extract. Prefer extract_cover_from_audio when possible."""
    return extract_cover_from_audio(MutagenFile(path))


def _get_cover_cached(path: str) -> tuple[bytes, str] | None:
    """Extract cover via cache. Single file open per path per session."""
    cached = _cache_lookup(path)
    if cached is not False:
        return cached

    result = _extract_cover_bytes(path)
    _cache_store(path, result)
    return result


# ------------------------------------------------------------ compression

def _extension_for(mime: str) -> str:
    if mime == "image/png":
        return ".png"
    if mime == "image/webp":
        return ".webp"
    return ".jpg"


def _compress_cover(image_bytes: bytes, mime: str) -> tuple[bytes, str]:
    if not _PIL_AVAILABLE:
        return image_bytes, mime

    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            if len(image_bytes) <= MAX_BYTES and max(im.size) <= MAX_DIMENSION:
                return image_bytes, mime

            im.load()

            if im.mode == "P":
                im = im.convert("RGBA")
            if im.mode == "LA":
                im = im.convert("RGBA")

            if im.mode == "RGBA":
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")

            if max(im.size) > MAX_DIMENSION:
                im.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            out = buf.getvalue()

            if len(out) >= len(image_bytes):
                return image_bytes, mime

            return out, "image/jpeg"
    except Exception as exc:
        print(f"Cover compression failed: {exc}")
        return image_bytes, mime


# ------------------------------------------------------------ public API

def get_cover_bytes(path: str) -> tuple[bytes, str] | None:
    """Instant: extract cover from the file, cached per path."""
    return _get_cover_cached(path)


def get_cached_cover_url(path: str) -> str | None:
    """Instant: return a previously uploaded URL, or None. Uses cache."""
    extracted = _get_cover_cached(path)
    if not extracted:
        return None

    image_bytes, _mime = extracted
    digest = hashlib.sha256(image_bytes).hexdigest()
    url_file = CACHE_DIR / f"{digest}.url"

    if url_file.exists():
        cached = url_file.read_text(encoding="utf-8").strip()
        if cached.startswith("http"):
            return cached

    return None


def _upload_to_catbox(image_bytes: bytes, ext: str) -> str | None:
    try:
        response = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (f"cover{ext}", image_bytes)},
            timeout=20,
        )
        response.raise_for_status()
        url = response.text.strip()
        if url.startswith("http"):
            return url
    except Exception as exc:
        print(f"Album art upload failed: {exc}")
    return None


def upload_cover(path: str) -> str | None:
    """Network: compress if needed, upload, cache URL. Uses cache."""
    extracted = _get_cover_cached(path)
    if not extracted:
        return None

    raw_bytes, raw_mime = extracted
    digest = hashlib.sha256(raw_bytes).hexdigest()

    url_file = CACHE_DIR / f"{digest}.url"
    if url_file.exists():
        cached = url_file.read_text(encoding="utf-8").strip()
        if cached.startswith("http"):
            return cached

    comp_file = CACHE_DIR / f"{digest}.compressed"
    mime_file = CACHE_DIR / f"{digest}.mime"

    if comp_file.exists() and mime_file.exists():
        compressed = comp_file.read_bytes()
        final_mime = mime_file.read_text(encoding="utf-8").strip() or raw_mime
    else:
        compressed, final_mime = _compress_cover(raw_bytes, raw_mime)
        comp_file.write_bytes(compressed)
        mime_file.write_text(final_mime, encoding="utf-8")

        if len(compressed) < len(raw_bytes):
            saved = 100 * (1 - len(compressed) / len(raw_bytes))
            print(
                f"Cover compressed: {len(raw_bytes):,} → "
                f"{len(compressed):,} bytes ({saved:.0f}% saved)"
            )

    url = _upload_to_catbox(compressed, _extension_for(final_mime))
    if url:
        url_file.write_text(url, encoding="utf-8")
    return url