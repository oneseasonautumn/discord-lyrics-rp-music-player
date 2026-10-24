import sys
from PyQt6.QtWidgets import QApplication

from ffmpeg_locator import ensure_ffmpeg
from player_ui import MediaPlayer


def main():
    app = QApplication(sys.argv)
    player = MediaPlayer()
    player.show()

    # Resolve ffmpeg after the window is visible so dialogs have a parent.
    ffmpeg_path = ensure_ffmpeg(parent=player)
    player.set_ffmpeg_path(ffmpeg_path)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()