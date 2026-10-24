from PyQt6.QtCore import QThread, pyqtSignal

from library import scan_folder


class LibraryScanner(QThread):
    """Scans a folder in the background and emits the resulting Library."""

    finished_scan = pyqtSignal(object)   # Library
    progress = pyqtSignal(int, int, str)  # current, total, filename

    def __init__(self, folder: str, parent=None):
        super().__init__(parent)
        self.folder = folder

    def run(self):
        try:
            lib = scan_folder(
                self.folder,
                progress=lambda c, t, n: self.progress.emit(c, t, n),
            )
            self.finished_scan.emit(lib)
        except Exception as exc:
            print(f"Library scan failed: {exc}")
            from library import Library
            self.finished_scan.emit(Library(folder=self.folder))