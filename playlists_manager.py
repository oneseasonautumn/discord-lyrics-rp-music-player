import json
from pathlib import Path


USER_PLAYLISTS_FILE = Path(__file__).resolve().parent / "user_playlists.json"


def load_user_playlists() -> dict[str, list[str]]:
    """Return {playlist_name: [track_path, ...]}."""
    if USER_PLAYLISTS_FILE.exists():
        try:
            data = json.loads(USER_PLAYLISTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): [str(p) for p in v if isinstance(p, str)]
                    for k, v in data.items()
                    if isinstance(v, list)
                }
        except Exception as exc:
            print(f"User playlists load failed: {exc}")
    return {}


def save_user_playlists(playlists: dict[str, list[str]]) -> None:
    try:
        USER_PLAYLISTS_FILE.write_text(
            json.dumps(playlists, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"User playlists save failed: {exc}")