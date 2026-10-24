import json
from pathlib import Path


CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_last_folder() -> str:
    return load_config().get("last_folder", "") or ""


def set_last_folder(path: str) -> None:
    config = load_config()
    config["last_folder"] = path
    save_config(config)


def get_shuffle() -> bool:
    return bool(load_config().get("shuffle", False))


def set_shuffle(value: bool) -> None:
    config = load_config()
    config["shuffle"] = bool(value)
    save_config(config)


def get_repeat_song() -> bool:
    return bool(load_config().get("repeat_song", False))


def set_repeat_song(value: bool) -> None:
    config = load_config()
    config["repeat_song"] = bool(value)
    save_config(config)


def get_ffmpeg_path() -> str:
    return load_config().get("ffmpeg_path", "") or ""


def set_ffmpeg_path(path: str) -> None:
    config = load_config()
    config["ffmpeg_path"] = path
    save_config(config)


def get_ffmpeg_prompted() -> bool:
    return bool(load_config().get("ffmpeg_prompted", False))


def set_ffmpeg_prompted(value: bool) -> None:
    config = load_config()
    config["ffmpeg_prompted"] = bool(value)
    save_config(config)