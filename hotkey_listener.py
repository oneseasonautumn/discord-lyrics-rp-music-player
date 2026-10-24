from PyQt6.QtCore import QObject, pyqtSignal
from pynput import keyboard


class MediaKeyListener(QObject):
    """Listens for global media keys in a background thread.

    Emits Qt signals so the UI thread can safely handle them.
    """

    play_pause_pressed = pyqtSignal()
    next_pressed = pyqtSignal()
    previous_pressed = pyqtSignal()
    stop_pressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None

    def start(self):
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_press(self, key):
        try:
            if key == keyboard.Key.media_play_pause:
                self.play_pause_pressed.emit()
            elif key == keyboard.Key.media_next:
                self.next_pressed.emit()
            elif key == keyboard.Key.media_previous:
                self.previous_pressed.emit()
            elif key == keyboard.Key.media_stop:
                self.stop_pressed.emit()
        except Exception:
            pass