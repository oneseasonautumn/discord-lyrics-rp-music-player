import os
import shutil
import subprocess


def _is_valid_ffmpeg(path: str) -> bool:
    """Check that the given path exists and actually runs as ffmpeg."""
    if not path or not os.path.isfile(path):
        return False
    try:
        result = subprocess.run(
            [path, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def find_in_path() -> str | None:
    """Return the ffmpeg path if it's on PATH and works, else None."""
    found = shutil.which("ffmpeg")
    if found and _is_valid_ffmpeg(found):
        return found
    return None


def find_noninteractive() -> str | None:
    """Config lookup, then PATH lookup. No dialogs.

    Safe to call from deploy.py without PyQt6 installed.
    """
    from config_manager import get_ffmpeg_path, set_ffmpeg_path

    saved = get_ffmpeg_path()
    if saved and _is_valid_ffmpeg(saved):
        return saved

    path_ffmpeg = find_in_path()
    if path_ffmpeg:
        set_ffmpeg_path(path_ffmpeg)
        return path_ffmpeg

    return None


def prompt_for_ffmpeg(parent=None) -> str | None:
    """Always ask the user to locate ffmpeg. Returns the path or None."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from config_manager import set_ffmpeg_path, set_ffmpeg_prompted

    reply = QMessageBox.question(
        parent,
        "Locate FFmpeg",
        "Select the FFmpeg executable.\n\n"
        "On Windows this is usually ffmpeg.exe. On Linux/macOS it is\n"
        "usually just 'ffmpeg' (no extension).",
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Ok,
    )
    if reply != QMessageBox.StandardButton.Ok:
        return None

    if os.name == "nt":
        file_filter = "FFmpeg binary (ffmpeg.exe);;All files (*)"
    else:
        file_filter = "FFmpeg binary (ffmpeg);;All files (*)"

    selected, _ = QFileDialog.getOpenFileName(
        parent,
        "Select FFmpeg binary",
        "",
        file_filter,
    )
    if not selected:
        return None

    if not _is_valid_ffmpeg(selected):
        QMessageBox.warning(
            parent,
            "Invalid FFmpeg",
            f"'{os.path.basename(selected)}' does not appear to be a\n"
            "working FFmpeg binary (ffmpeg -version failed).\n\n"
            "Try another file, or reinstall FFmpeg.",
        )
        return None

    set_ffmpeg_path(selected)
    set_ffmpeg_prompted(True)
    return selected


def ensure_ffmpeg(parent=None) -> str | None:
    """Resolve ffmpeg once on startup.

    Order:
      1. Valid path from config.json
      2. shutil.which("ffmpeg") on PATH (save to config if found)
      3. Already prompted before  ->  return None silently
      4. Prompt the user once, save result
    """
    from config_manager import (
        get_ffmpeg_path,
        set_ffmpeg_path,
        get_ffmpeg_prompted,
        set_ffmpeg_prompted,
    )
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    # 1. Saved path
    saved = get_ffmpeg_path()
    if saved and _is_valid_ffmpeg(saved):
        return saved

    # 2. PATH
    path_ffmpeg = find_in_path()
    if path_ffmpeg:
        set_ffmpeg_path(path_ffmpeg)
        return path_ffmpeg

    # 3. Already prompted before — don't nag
    if get_ffmpeg_prompted():
        return None

    # 4. Prompt once
    reply = QMessageBox.question(
        parent,
        "FFmpeg not found",
        "FFmpeg was not found in your PATH.\n\n"
        "Some features (format conversion, additional metadata extraction)\n"
        "may not work without it.\n\n"
        "Would you like to locate the FFmpeg binary now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )

    # Mark that we've asked, so we don't prompt again on next launch.
    set_ffmpeg_prompted(True)

    if reply != QMessageBox.StandardButton.Yes:
        return None

    if os.name == "nt":
        file_filter = "FFmpeg binary (ffmpeg.exe);;All files (*)"
    else:
        file_filter = "FFmpeg binary (ffmpeg);;All files (*)"

    selected, _ = QFileDialog.getOpenFileName(
        parent,
        "Select FFmpeg binary",
        "",
        file_filter,
    )
    if not selected:
        return None

    if not _is_valid_ffmpeg(selected):
        QMessageBox.warning(
            parent,
            "Invalid FFmpeg",
            f"'{os.path.basename(selected)}' does not appear to be a\n"
            "working FFmpeg binary (ffmpeg -version failed).\n\n"
            "You can set the correct path by editing config.json.",
        )
        return None

    set_ffmpeg_path(selected)
    return selected