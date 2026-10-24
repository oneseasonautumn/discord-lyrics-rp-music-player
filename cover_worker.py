from PyQt6.QtCore import QThread, pyqtSignal

from album_art import upload_cover


class CoverUploadWorker(QThread):
    """Uploads the cover for one track in the background.

    Emits (track_path, url_or_empty) when finished.
    """

    finished_upload = pyqtSignal(str, str)

    def __init__(self, track_path: str, parent=None):
        super().__init__(parent)
        self.track_path = track_path

    def run(self):
        url = upload_cover(self.track_path) or ""
        self.finished_upload.emit(self.track_path, url)