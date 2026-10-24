import os

from pypresence import Presence

from config_manager import load_config


DEFAULT_ASSET_KEY = "music"


def _resolve_client_id() -> str:
    config = load_config()
    value = (config.get("discord_app_id") or "").strip()
    if value:
        return value

    env_value = os.getenv("DISCORD_APP_ID", "").strip()
    if env_value:
        return env_value

    return "YOUR_APP_ID"


class DiscordRPC:
    def __init__(self):
        self.client_id = _resolve_client_id()
        self.rpc: Presence | None = None
        self.connected = False
        self.last_state: str | None = None
        self.last_image: str | None = None

        if self.client_id and self.client_id != "YOUR_APP_ID":
            try:
                self.rpc = Presence(self.client_id)
                self.rpc.connect()
                self.connected = True
            except Exception as exc:
                print(f"Discord RPC connection failed: {exc}")
                self.rpc = None
                self.connected = False
        else:
            print("Discord RPC disabled: no Application ID configured.")

    # ------------------------------------------------------------ internal

    def _close_silent(self) -> None:
        """Close the existing pipe without raising."""
        if self.rpc is not None:
            try:
                self.rpc.close()
            except Exception:
                pass
        self.rpc = None
        self.connected = False

    # ------------------------------------------------------------ public

    def has_client_id(self) -> bool:
        return bool(self.client_id and self.client_id != "YOUR_APP_ID")

    def reconnect(self) -> bool:
        """Close the current connection and open a fresh one.

        Returns True on success, False on failure.
        Clears the last-pushed state so the next update() actually fires.
        """
        self._close_silent()

        if not self.has_client_id():
            return False

        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.connected = True
            self.last_state = None
            self.last_image = None
            print("Discord RPC reconnected.")
            return True
        except Exception as exc:
            print(f"Discord RPC reconnect failed: {exc}")
            self.rpc = None
            self.connected = False
            return False

    def update(
        self,
        details: str,
        state: str,
        start_epoch: int | None = None,
        large_image: str | None = None,
        large_text: str = "",
    ) -> None:
        if not self.connected or self.rpc is None:
            return

        state = (state or "No lyrics available")[:128]
        details = details[:128]
        image = large_image or DEFAULT_ASSET_KEY

        if state == self.last_state and image == self.last_image:
            return

        try:
            self.rpc.update(
                details=details,
                state=state,
                start=start_epoch,
                large_image=image,
                large_text=large_text[:128],
            )
            self.last_state = state
            self.last_image = image
        except Exception as exc:
            print(f"Discord RPC update failed: {exc}")
            # The pipe is probably dead. Mark disconnected so the user
            # can hit the Reconnect button.
            self.connected = False

    def close(self) -> None:
        self._close_silent()