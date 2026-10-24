import os
import random
import time

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QPixmap, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QSplitter,
    QFileDialog,
    QSlider,
    QMenu,
    QLineEdit,
    QInputDialog,
    QMessageBox,
)
from mutagen import File as MutagenFile

from lyrics_parser import parse_lrc, current_line, LyricLine
from discord_rpc import DiscordRPC
from album_art import (
    get_cover_bytes,
    get_cached_cover_url,
    extract_cover_from_audio,
    cache_cover,
)
from config_manager import (
    get_last_folder,
    set_last_folder,
    get_shuffle,
    set_shuffle,
    get_repeat_song,
    set_repeat_song,
)
from cover_worker import CoverUploadWorker
from hotkey_listener import MediaKeyListener
from playlists_manager import load_user_playlists, save_user_playlists
from library import (
    Library,
    Track,
    read_tag,
    read_lyrics,
    TITLE_KEYS,
    ARTIST_KEYS,
    ALBUM_KEYS,
)
from library_worker import LibraryScanner


class MediaPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lyric Media Player")
        self.resize(1200, 800)

        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.8)

        self.rpc = DiscordRPC()

        # Track state
        self.lyrics: list[LyricLine] = []
        self.current_idx = -1
        self.start_epoch = None
        self.current_file = None
        self.title = ""
        self.artist = ""
        self.album = ""
        self.cover_url = None
        self._cover_pixmap = None

        # FFmpeg path (set by main.py after ensure_ffmpeg)
        self.ffmpeg_path: str | None = None

        # Background workers
        self._cover_worker: CoverUploadWorker | None = None
        self._scanner: LibraryScanner | None = None

        # Library
        self.library: Library | None = None

        # Queue
        self.queue: list[str] = []
        self.queue_index = -1

        # Shuffle state
        self.shuffle_enabled: bool = get_shuffle()
        self._shuffle_bag: list[int] = []
        self._shuffle_history: list[int] = []

        # Repeat-song state
        self.repeat_song_enabled: bool = get_repeat_song()

        # User playlists
        self.user_playlists: dict[str, list[str]] = load_user_playlists()

        # Media key listener
        self._media_keys = MediaKeyListener(self)
        self._connect_media_keys()
        self._media_keys.start()

        self._build_ui()
        self._connect_signals()

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._on_tick)

        self._update_shuffle_button()
        self._update_repeat_button()
        self._restore_last_folder()

    # ------------------------------------------------------------ ffmpeg

    def set_ffmpeg_path(self, path: str | None) -> None:
        self.ffmpeg_path = path
        if path:
            self.statusBar().showMessage(f"FFmpeg: {path}", 5000)
        else:
            self.statusBar().showMessage("FFmpeg: not found (optional)", 5000)

    # ------------------------------------------------------------ media keys

    def _connect_media_keys(self):
        self._media_keys.play_pause_pressed.connect(self._toggle_play_pause)
        self._media_keys.next_pressed.connect(self.play_next)
        self._media_keys.previous_pressed.connect(self.play_prev)
        self._media_keys.stop_pressed.connect(self.stop)

    def _toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.play()

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ----- Left: sidebar -----
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(4)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search title, artist, album…")
        self.search_input.setClearButtonEnabled(True)
        search_row.addWidget(self.search_input)
        sidebar_layout.addLayout(search_row)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setMinimumWidth(280)

        self.artist_tree = QTreeWidget()
        self.artist_tree.setHeaderHidden(True)
        self.artist_tree.itemDoubleClicked.connect(self._on_artist_item_double_click)
        self.artist_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.artist_tree.customContextMenuRequested.connect(
            self._on_artist_context_menu
        )
        self.sidebar_tabs.addTab(self.artist_tree, "Artists")

        self.album_list = QListWidget()
        self.album_list.itemDoubleClicked.connect(self._on_album_double_click)
        self.album_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.album_list.customContextMenuRequested.connect(
            self._on_album_context_menu
        )
        self.sidebar_tabs.addTab(self.album_list, "Albums")

        self.playlist_list = QListWidget()
        self.playlist_list.itemDoubleClicked.connect(self._on_playlist_double_click)
        self.playlist_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.playlist_list.customContextMenuRequested.connect(
            self._on_playlist_context_menu
        )
        self.sidebar_tabs.addTab(self.playlist_list, "Playlists")

        self.search_list = QListWidget()
        self.search_list.itemDoubleClicked.connect(self._on_search_double_click)
        self.search_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.search_list.customContextMenuRequested.connect(
            self._on_search_context_menu
        )
        self.sidebar_tabs.addTab(self.search_list, "Search")

        sidebar_layout.addWidget(self.sidebar_tabs)
        splitter.addWidget(sidebar)

        # ----- Right: player -----
        right = QWidget()
        layout = QVBoxLayout(right)
        right.setMinimumWidth(560)

        header = QHBoxLayout()

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(180, 180)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet(
            "background-color: #222; border-radius: 8px; color: #666;"
        )
        self.cover_label.setText("♪")
        header.addWidget(self.cover_label)

        info_box = QVBoxLayout()
        info_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel("No track loaded")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        info_box.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setStyleSheet("font-size: 15px; color: #aaa;")
        info_box.addWidget(self.artist_label)

        self.album_label = QLabel("")
        self.album_label.setStyleSheet("font-size: 13px; color: #888;")
        info_box.addWidget(self.album_label)

        info_box.addStretch()
        header.addLayout(info_box, stretch=1)
        layout.addLayout(header)

        self.lyric_label = QLabel("♪ No lyrics ♪")
        self.lyric_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyric_label.setWordWrap(True)
        self.lyric_label.setStyleSheet(
            "font-size: 24px; color: #1DB954; padding: 16px;"
        )
        layout.addWidget(self.lyric_label)

        queue_header = QLabel("Queue")
        queue_header.setStyleSheet("font-weight: bold; padding-top: 6px;")
        layout.addWidget(queue_header)

        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(130)
        self.queue_list.itemDoubleClicked.connect(self._on_queue_double_click)
        self.queue_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.queue_list.customContextMenuRequested.connect(
            self._on_queue_context_menu
        )
        layout.addWidget(self.queue_list)

        lyric_header = QLabel("Lyric Timeline")
        lyric_header.setStyleSheet("font-weight: bold; padding-top: 6px;")
        layout.addWidget(lyric_header)

        self.playlist = QListWidget()
        self.playlist.itemClicked.connect(self._on_playlist_click)
        layout.addWidget(self.playlist)

        controls = QHBoxLayout()
        self.open_btn = QPushButton("Open Folder")
        self.prev_btn = QPushButton("Prev")
        self.play_btn = QPushButton("Play")
        self.pause_btn = QPushButton("Pause")
        self.next_btn = QPushButton("Next")
        self.stop_btn = QPushButton("Stop")
        self.shuffle_btn = QPushButton("Shuffle: Off")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.setChecked(self.shuffle_enabled)
        self.repeat_btn = QPushButton("Repeat: Off")
        self.repeat_btn.setCheckable(True)
        self.repeat_btn.setChecked(self.repeat_song_enabled)
        self.discord_btn = QPushButton("Reconnect Discord")
        self.discord_btn.setToolTip(
            "Close and reopen the Discord RPC connection.\n"
            "Use this if your Discord Rich Presence stops updating."
        )

        for btn in (
            self.open_btn,
            self.prev_btn,
            self.play_btn,
            self.pause_btn,
            self.next_btn,
            self.stop_btn,
            self.shuffle_btn,
            self.repeat_btn,
            self.discord_btn,
        ):
            controls.addWidget(btn)
        layout.addLayout(controls)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        layout.addWidget(self.position_slider)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 880])

    def _connect_signals(self):
        self.open_btn.clicked.connect(self.open_folder)
        self.prev_btn.clicked.connect(self.play_prev)
        self.play_btn.clicked.connect(self.play)
        self.pause_btn.clicked.connect(self.player.pause)
        self.next_btn.clicked.connect(self.play_next)
        self.stop_btn.clicked.connect(self.stop)
        self.shuffle_btn.toggled.connect(self._on_shuffle_toggled)
        self.repeat_btn.toggled.connect(self._on_repeat_toggled)
        self.discord_btn.clicked.connect(self._on_discord_reconnect_clicked)
        self.search_input.textChanged.connect(self._on_search_changed)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.position_slider.sliderMoved.connect(self.player.setPosition)

    # ------------------------------------------------------------ discord reconnect

    def _on_discord_reconnect_clicked(self):
        if not self.rpc.has_client_id():
            QMessageBox.warning(
                self,
                "Discord RPC",
                "No Discord Application ID is configured.\n\n"
                "Run deploy.py and enter your App ID, or edit config.json "
                "and set \"discord_app_id\".",
            )
            return

        self.discord_btn.setEnabled(False)
        self.discord_btn.setText("Connecting…")
        # Let the label render before the blocking connect() call.
        QApplication.processEvents()

        ok = self.rpc.reconnect()

        self.discord_btn.setEnabled(True)
        self.discord_btn.setText("Reconnect Discord")

        if ok:
            self.statusBar().showMessage("Discord RPC reconnected", 4000)
            # Re-push the current state so Discord updates immediately.
            if self.current_file:
                self._push_rpc_now()
        else:
            QMessageBox.warning(
                self,
                "Discord RPC",
                "Could not connect to Discord.\n\n"
                "Make sure the Discord desktop client is running and "
                "logged in, then try again.",
            )

    # ------------------------------------------------------------ shuffle

    def _on_shuffle_toggled(self, checked: bool):
        self.shuffle_enabled = checked
        set_shuffle(checked)
        self._update_shuffle_button()
        self._reset_shuffle_bag()

    def _update_shuffle_button(self):
        if self.shuffle_enabled:
            self.shuffle_btn.setText("Shuffle: On")
            self.shuffle_btn.setStyleSheet(
                "background-color: #1DB954; color: white; font-weight: bold;"
            )
        else:
            self.shuffle_btn.setText("Shuffle: Off")
            self.shuffle_btn.setStyleSheet("")

    def _reset_shuffle_bag(self):
        self._shuffle_bag = list(range(len(self.queue)))
        if self.queue_index in self._shuffle_bag:
            self._shuffle_bag.remove(self.queue_index)
        random.shuffle(self._shuffle_bag)
        self._shuffle_history = []

    # ------------------------------------------------------------ repeat song

    def _on_repeat_toggled(self, checked: bool):
        self.repeat_song_enabled = checked
        set_repeat_song(checked)
        self._update_repeat_button()

    def _update_repeat_button(self):
        if self.repeat_song_enabled:
            self.repeat_btn.setText("Repeat: On")
            self.repeat_btn.setStyleSheet(
                "background-color: #1DB954; color: white; font-weight: bold;"
            )
        else:
            self.repeat_btn.setText("Repeat: Off")
            self.repeat_btn.setStyleSheet("")

    def _repeat_current_track(self) -> None:
        if not self.current_file:
            return

        self.player.setPosition(0)
        self.current_idx = -1
        self.lyric_label.setText("♪ No lyrics ♪")
        self.start_epoch = int(time.time())

        self.playlist.setCurrentRow(-1)
        self.playlist.clearSelection()

        self.player.play()
        self.timer.start()

    # ------------------------------------------------------------ search

    def _on_search_changed(self, text: str):
        query = text.strip().lower()
        self.search_list.clear()

        if not query:
            if self.sidebar_tabs.currentWidget() is self.search_list:
                self.sidebar_tabs.setCurrentWidget(self.artist_tree)
            return

        if not self.library:
            return

        results: list[Track] = []
        for t in self.library.tracks:
            if (
                query in t.title.lower()
                or query in t.artist.lower()
                or query in t.album.lower()
            ):
                results.append(t)
                if len(results) >= 500:
                    break

        if not results:
            placeholder = QListWidgetItem("No results")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.search_list.addItem(placeholder)
        else:
            for t in results:
                label = f"{t.artist} — {t.title}"
                if t.album:
                    label += f"   [{t.album}]"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, t.path)
                self.search_list.addItem(item)

        self.sidebar_tabs.setCurrentWidget(self.search_list)

    def _on_search_double_click(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._play_tracks([path], 0)

    def _on_search_context_menu(self, pos):
        item = self.search_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return

        menu, play_a, next_a, queue_a = self._build_track_menu()
        self._add_playlist_submenu(menu, [path])
        chosen = menu.exec(self.search_list.viewport().mapToGlobal(pos))
        self._handle_track_menu_choice(chosen, [path], play_a, next_a, queue_a)

    # ------------------------------------------------------------ folder / library

    def _restore_last_folder(self):
        last = get_last_folder()
        if last and os.path.isdir(last):
            self._load_folder(last)

    def open_folder(self):
        start_dir = get_last_folder() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(
            self, "Open Music Folder", start_dir
        )
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder: str):
        set_last_folder(folder)
        self.title_label.setText(f"Scanning {os.path.basename(folder)}...")
        self.artist_label.setText("")
        self.album_label.setText("")
        self.queue_list.clear()
        self.playlist.clear()
        self.artist_tree.clear()
        self.album_list.clear()
        self.playlist_list.clear()
        self.search_list.clear()

        self._scanner = LibraryScanner(folder, self)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.finished_scan.connect(self._on_scan_finished)
        self._scanner.start()

    def _on_scan_progress(self, current: int, total: int, name: str):
        if total:
            self.title_label.setText(f"Scanning… {current}/{total} — {name}")

    def _on_scan_finished(self, library: Library):
        self.library = library
        self.setWindowTitle(
            f"Lyric Media Player — {os.path.basename(library.folder)}"
        )
        self._refresh_sidebar()

        if library.tracks:
            paths = [t.path for t in library.tracks]
            self._set_queue(paths, start_index=0, autoplay=False)
        else:
            self.title_label.setText("No audio files found")

    # ------------------------------------------------------------ sidebar

    def _refresh_sidebar(self):
        if not self.library:
            return

        self.artist_tree.clear()
        for artist in sorted(self.library.by_artist.keys(), key=str.lower):
            tracks = self.library.by_artist[artist]

            albums: dict[str, list[Track]] = {}
            for t in tracks:
                albums.setdefault(t.album or "Unknown Album", []).append(t)

            artist_item = QTreeWidgetItem([f"{artist}  ({len(tracks)})"])
            artist_item.setData(0, Qt.ItemDataRole.UserRole, ("artist", artist))

            for album in sorted(albums.keys(), key=str.lower):
                album_tracks = albums[album]
                album_item = QTreeWidgetItem(
                    [f"{album}  ({len(album_tracks)})"]
                )
                album_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    ("album-tracks", [t.path for t in album_tracks]),
                )
                for t in album_tracks:
                    track_item = QTreeWidgetItem([t.title])
                    track_item.setData(
                        0, Qt.ItemDataRole.UserRole, ("track", t.path)
                    )
                    album_item.addChild(track_item)
                artist_item.addChild(album_item)

            self.artist_tree.addTopLevelItem(artist_item)

        self.album_list.clear()
        for album in sorted(self.library.by_album.keys(), key=str.lower):
            tracks = self.library.by_album[album]
            item = QListWidgetItem(f"{album}  ({len(tracks)})")
            item.setData(Qt.ItemDataRole.UserRole, [t.path for t in tracks])
            self.album_list.addItem(item)

        self.playlist_list.clear()

        new_item = QListWidgetItem("+  New Playlist…")
        new_item.setData(Qt.ItemDataRole.UserRole, ("__new_playlist__", None))
        self.playlist_list.addItem(new_item)

        for name in sorted(self.user_playlists.keys(), key=str.lower):
            paths = self.user_playlists[name]
            item = QListWidgetItem(f"✎  {name}  ({len(paths)})")
            item.setData(
                Qt.ItemDataRole.UserRole,
                ("__user_playlist__", name),
            )
            self.playlist_list.addItem(item)

        for pl in self.library.playlists:
            label = f"{pl.name}  ({len(pl.tracks)})"
            if pl.path is None:
                label = "★ " + label
            else:
                label = "♪ " + label
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ("__file_playlist__", pl.tracks))
            self.playlist_list.addItem(item)

        n_tracks = len(self.library.tracks)
        n_timed = sum(1 for t in self.library.tracks if t.has_timed_lyrics)
        self.title_label.setText(f"{n_tracks} tracks, {n_timed} with timed lyrics")

    # ------------------------------------------------------------ sidebar double-click

    def _on_artist_item_double_click(self, item: QTreeWidgetItem, _column: int):
        paths = self._paths_from_artist_item(item)
        if paths:
            self._play_tracks(paths, 0)

    def _on_album_double_click(self, item: QListWidgetItem):
        paths = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(paths, list):
            self._play_tracks(paths, 0)

    def _on_playlist_double_click(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, value = data

        if kind == "__new_playlist__":
            self._create_playlist()
            return

        if kind == "__user_playlist__":
            paths = self.user_playlists.get(value, [])
            paths = [p for p in paths if os.path.isfile(p)]
            if not paths:
                QMessageBox.information(
                    self, "Empty playlist", f"'{value}' has no playable tracks."
                )
                return
            self._play_tracks(paths, 0)
            return

        if kind == "__file_playlist__":
            paths = [p for p in value if os.path.isfile(p)]
            if paths:
                self._play_tracks(paths, 0)

    def _paths_from_artist_item(self, item: QTreeWidgetItem) -> list[str]:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return []
        kind, value = data
        if kind == "track":
            return [value]
        if kind == "album-tracks":
            return list(value)
        if kind == "artist":
            if not self.library:
                return []
            return [t.path for t in self.library.by_artist.get(value, [])]
        return []

    # ------------------------------------------------------------ context menus

    def _build_track_menu(self) -> tuple[QMenu, QAction, QAction, QAction]:
        menu = QMenu(self)
        play_action = menu.addAction("Play Now")
        next_action = menu.addAction("Play Next")
        queue_action = menu.addAction("Add to Queue")
        return menu, play_action, next_action, queue_action

    def _add_playlist_submenu(self, menu: QMenu, paths: list[str]) -> QAction:
        submenu = menu.addMenu("Add to Playlist")

        for name in sorted(self.user_playlists.keys(), key=str.lower):
            action = submenu.addAction(name)
            action.setData(("add_to", name))

        if self.user_playlists:
            submenu.addSeparator()

        new_action = submenu.addAction("New Playlist…")
        new_action.setData(("new_with", None))

        return submenu.menuAction()

    def _handle_track_menu_choice(
        self,
        chosen: QAction | None,
        paths: list[str],
        play_a: QAction,
        next_a: QAction,
        queue_a: QAction,
    ):
        if chosen is None:
            return

        data = chosen.data()
        if isinstance(data, tuple) and len(data) == 2:
            kind, value = data
            if kind == "add_to":
                self._add_paths_to_playlist(value, paths)
            elif kind == "new_with":
                self._create_playlist_with(paths)
            return

        if chosen == play_a:
            self._play_tracks(paths, 0)
        elif chosen == next_a:
            self._insert_play_next(paths)
        elif chosen == queue_a:
            self._append_to_queue(paths)

    def _on_artist_context_menu(self, pos):
        item = self.artist_tree.itemAt(pos)
        if not item:
            return
        paths = self._paths_from_artist_item(item)
        if not paths:
            return

        menu, play_a, next_a, queue_a = self._build_track_menu()
        self._add_playlist_submenu(menu, paths)
        chosen = menu.exec(self.artist_tree.viewport().mapToGlobal(pos))
        self._handle_track_menu_choice(chosen, paths, play_a, next_a, queue_a)

    def _on_album_context_menu(self, pos):
        item = self.album_list.itemAt(pos)
        if not item:
            return
        paths = item.data(Qt.ItemDataRole.UserRole) or []
        if not isinstance(paths, list):
            return

        menu, play_a, next_a, queue_a = self._build_track_menu()
        self._add_playlist_submenu(menu, paths)
        chosen = menu.exec(self.album_list.viewport().mapToGlobal(pos))
        self._handle_track_menu_choice(chosen, paths, play_a, next_a, queue_a)

    def _on_playlist_context_menu(self, pos):
        item = self.playlist_list.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, value = data

        if kind == "__new_playlist__":
            menu = QMenu(self)
            new_a = menu.addAction("New Playlist…")
            chosen = menu.exec(self.playlist_list.viewport().mapToGlobal(pos))
            if chosen == new_a:
                self._create_playlist()
            return

        if kind == "__user_playlist__":
            menu = QMenu(self)
            play_a = menu.addAction("Play Now")
            next_a = menu.addAction("Play Next")
            queue_a = menu.addAction("Add to Queue")
            menu.addSeparator()
            rename_a = menu.addAction("Rename Playlist…")
            delete_a = menu.addAction("Delete Playlist")
            chosen = menu.exec(self.playlist_list.viewport().mapToGlobal(pos))

            paths = [p for p in self.user_playlists.get(value, []) if os.path.isfile(p)]

            if chosen == play_a:
                if paths:
                    self._play_tracks(paths, 0)
            elif chosen == next_a:
                if paths:
                    self._insert_play_next(paths)
            elif chosen == queue_a:
                if paths:
                    self._append_to_queue(paths)
            elif chosen == rename_a:
                self._rename_playlist(value)
            elif chosen == delete_a:
                self._delete_playlist(value)
            return

        if kind == "__file_playlist__":
            paths = [p for p in value if os.path.isfile(p)]
            if not paths:
                return
            menu, play_a, next_a, queue_a = self._build_track_menu()
            self._add_playlist_submenu(menu, paths)
            chosen = menu.exec(self.playlist_list.viewport().mapToGlobal(pos))
            self._handle_track_menu_choice(chosen, paths, play_a, next_a, queue_a)

    def _on_queue_context_menu(self, pos):
        item = self.queue_list.itemAt(pos)
        if not item:
            return
        row = self.queue_list.row(item)
        if not (0 <= row < len(self.queue)):
            return
        path = self.queue[row]

        menu = QMenu(self)
        play_action = menu.addAction("Play Now")
        play_next_action = menu.addAction("Play Next")
        menu.addSeparator()
        remove_action = menu.addAction("Remove from Queue")
        menu.addSeparator()
        self._add_playlist_submenu(menu, [path])

        chosen = menu.exec(self.queue_list.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        data = chosen.data()
        if isinstance(data, tuple) and len(data) == 2:
            kind, value = data
            if kind == "add_to":
                self._add_paths_to_playlist(value, [path])
            elif kind == "new_with":
                self._create_playlist_with([path])
            return

        if chosen == play_action:
            self._jump_to_queue_index(row)
        elif chosen == play_next_action:
            self._move_queue_row_next(row)
        elif chosen == remove_action:
            self._remove_from_queue(row)

    # ------------------------------------------------------------ playlist management

    def _create_playlist(self):
        name, ok = QInputDialog.getText(
            self, "New Playlist", "Playlist name:"
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in self.user_playlists:
            QMessageBox.warning(
                self, "Already exists",
                f"A playlist named '{name}' already exists.",
            )
            return
        self.user_playlists[name] = []
        save_user_playlists(self.user_playlists)
        self._refresh_sidebar()
        self.statusBar().showMessage(f"Created playlist '{name}'", 3000)

    def _create_playlist_with(self, paths: list[str]):
        name, ok = QInputDialog.getText(
            self, "New Playlist", "Playlist name:"
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in self.user_playlists:
            reply = QMessageBox.question(
                self,
                "Already exists",
                f"'{name}' already exists. Add these tracks to it?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            self.user_playlists[name] = []

        added = 0
        existing = set(self.user_playlists[name])
        for p in paths:
            if p in existing:
                continue
            self.user_playlists[name].append(p)
            existing.add(p)
            added += 1

        save_user_playlists(self.user_playlists)
        self._refresh_sidebar()
        self.statusBar().showMessage(
            f"Added {added} track(s) to '{name}'", 3000
        )

    def _add_paths_to_playlist(self, name: str, paths: list[str]):
        if name not in self.user_playlists:
            self.user_playlists[name] = []

        added = 0
        existing = set(self.user_playlists[name])
        for p in paths:
            if p in existing:
                continue
            self.user_playlists[name].append(p)
            existing.add(p)
            added += 1

        save_user_playlists(self.user_playlists)
        self._refresh_sidebar()
        self.statusBar().showMessage(
            f"Added {added} track(s) to '{name}'" if added
            else f"No new tracks for '{name}'",
            3000,
        )

    def _rename_playlist(self, old_name: str):
        new_name, ok = QInputDialog.getText(
            self, "Rename Playlist", "New name:", text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if new_name in self.user_playlists:
            QMessageBox.warning(
                self, "Already exists",
                f"A playlist named '{new_name}' already exists.",
            )
            return

        self.user_playlists[new_name] = self.user_playlists.pop(old_name)
        save_user_playlists(self.user_playlists)
        self._refresh_sidebar()
        self.statusBar().showMessage(f"Renamed to '{new_name}'", 3000)

    def _delete_playlist(self, name: str):
        if name not in self.user_playlists:
            return
        reply = QMessageBox.question(
            self, "Delete Playlist",
            f"Delete playlist '{name}'?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self.user_playlists[name]
        save_user_playlists(self.user_playlists)
        self._refresh_sidebar()
        self.statusBar().showMessage(f"Deleted playlist '{name}'", 3000)

    # ------------------------------------------------------------ queue

    def _set_queue(self, paths: list[str], start_index: int, autoplay: bool):
        self.queue = list(paths)
        self.queue_index = start_index
        self._refresh_queue_widget()

        if self.queue:
            self.queue_list.setCurrentRow(start_index)
            self._load_track(self.queue[start_index])
            self._reset_shuffle_bag()
            if autoplay:
                self.play()

    def _refresh_queue_widget(self):
        self.queue_list.clear()
        for p in self.queue:
            t = self.library.by_path.get(p) if self.library else None
            if t:
                self.queue_list.addItem(f"{t.artist} — {t.title}")
            else:
                self.queue_list.addItem(os.path.basename(p))

    def _append_to_queue(self, paths: list[str]):
        if not paths:
            return

        existing = set(self.queue)
        added = 0
        for p in paths:
            if p in existing:
                continue
            self.queue.append(p)
            existing.add(p)
            added += 1

        self._refresh_queue_widget()

        if 0 <= self.queue_index < len(self.queue):
            self.queue_list.setCurrentRow(self.queue_index)

        if self.shuffle_enabled and added:
            new_indices = list(
                range(len(self.queue) - added, len(self.queue))
            )
            random.shuffle(new_indices)
            self._shuffle_bag.extend(new_indices)

        if self.current_file is None and self.queue:
            self.queue_index = 0
            self._load_track(self.queue[0])
            self.queue_list.setCurrentRow(0)

    def _insert_play_next(self, paths: list[str]):
        if not paths:
            return

        if self.current_file is None or self.queue_index < 0:
            self._play_tracks(paths, 0)
            return

        insert_at = self.queue_index + 1

        existing = set(self.queue)
        to_insert: list[str] = []
        for p in paths:
            if p in existing:
                continue
            if p in to_insert:
                continue
            to_insert.append(p)

        if not to_insert:
            return

        for offset, p in enumerate(to_insert):
            self.queue.insert(insert_at + offset, p)
            existing.add(p)

        if self.shuffle_enabled:
            new_indices = list(
                range(insert_at, insert_at + len(to_insert))
            )
            self._shuffle_bag = new_indices + self._shuffle_bag

        self._refresh_queue_widget()
        self.queue_list.setCurrentRow(self.queue_index)

    def _move_queue_row_next(self, row: int):
        if not (0 <= row < len(self.queue)):
            return
        if self.current_file is None or self.queue_index < 0:
            self._jump_to_queue_index(row)
            return
        if row == self.queue_index:
            return

        path = self.queue.pop(row)

        if row < self.queue_index:
            self.queue_index -= 1

        insert_at = self.queue_index + 1
        self.queue.insert(insert_at, path)

        self._reset_shuffle_bag()
        if self.queue_index in self._shuffle_bag:
            self._shuffle_bag.remove(self.queue_index)
        if self.shuffle_enabled:
            if insert_at in self._shuffle_bag:
                self._shuffle_bag.remove(insert_at)
            self._shuffle_bag.insert(0, insert_at)

        self._refresh_queue_widget()
        self.queue_list.setCurrentRow(self.queue_index)

    def _remove_from_queue(self, row: int):
        if not (0 <= row < len(self.queue)):
            return

        self.queue.pop(row)

        if row < self.queue_index:
            self.queue_index -= 1
        elif row == self.queue_index:
            self.queue_index = min(self.queue_index, len(self.queue) - 1)

        self._refresh_queue_widget()
        if 0 <= self.queue_index < len(self.queue):
            self.queue_list.setCurrentRow(self.queue_index)

        self._reset_shuffle_bag()

    def _jump_to_queue_index(self, row: int):
        if not (0 <= row < len(self.queue)):
            return
        self.queue_index = row
        self.queue_list.setCurrentRow(row)
        self._load_track(self.queue[row])
        self.play()

    def _play_tracks(self, paths: list[str], start_index: int = 0):
        if not paths:
            return
        self._set_queue(paths, start_index, autoplay=True)

    # ------------------------------------------------------------ cover

    def _show_cover_from_bytes(self, image_bytes: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_bytes):
            self._show_default_cover()
            return
        scaled = pixmap.scaled(
            self.cover_label.width(),
            self.cover_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._cover_pixmap = scaled
        self.cover_label.setPixmap(scaled)

    def _show_default_cover(self) -> None:
        self._cover_pixmap = None
        self.cover_label.clear()
        self.cover_label.setText("♪")

    def _start_cover_upload(self, path: str) -> None:
        cached = get_cached_cover_url(path)
        if cached:
            self.cover_url = cached
            self._push_rpc_now()
            return
        self._cover_worker = CoverUploadWorker(path, self)
        self._cover_worker.finished_upload.connect(self._on_cover_uploaded)
        self._cover_worker.start()

    def _on_cover_uploaded(self, track_path: str, url: str) -> None:
        if track_path != self.current_file:
            return
        if url:
            self.cover_url = url
            self._push_rpc_now()

    def _push_rpc_now(self) -> None:
        if not self.current_file:
            return
        state = "No lyrics available"
        if self.lyrics:
            _, line = current_line(self.lyrics, self.player.position())
            if line:
                state = line.text
        self.rpc.update(
            details=f"{self.artist} — {self.title}",
            state=state,
            start_epoch=self.start_epoch,
            large_image=self.cover_url or "music",
            large_text=self.album or self.title,
        )

    # ------------------------------------------------------------ track load

    def _load_track(self, path: str):
        self.stop(silent=True)
        self.current_file = path
        self.title = os.path.splitext(os.path.basename(path))[0]
        self.artist = "Unknown Artist"
        self.album = ""
        self.lyrics = []
        self.cover_url = None

        cached_track = self.library.by_path.get(path) if self.library else None

        cover = None
        try:
            audio = MutagenFile(path)
            if audio:
                found_title = read_tag(audio, TITLE_KEYS)
                if found_title:
                    self.title = found_title
                found_artist = read_tag(audio, ARTIST_KEYS)
                if found_artist:
                    self.artist = found_artist
                self.album = read_tag(audio, ALBUM_KEYS)

                lyrics_text = read_lyrics(audio)
                if lyrics_text:
                    self.lyrics = parse_lrc(lyrics_text)

                cover = extract_cover_from_audio(audio)
                cache_cover(path, cover)
        except Exception as exc:
            print(f"Metadata read error: {exc}")

        if cached_track:
            if self.title.startswith(os.path.splitext(os.path.basename(path))[0]):
                self.title = cached_track.title
            if self.artist == "Unknown Artist" and cached_track.artist:
                self.artist = cached_track.artist
            if not self.album and cached_track.album:
                self.album = cached_track.album

        if cover:
            self._show_cover_from_bytes(cover[0])
        else:
            self._show_default_cover()

        self.title_label.setText(self.title)
        self.artist_label.setText(self.artist)
        self.album_label.setText(self.album)

        self.playlist.clear()
        if self.lyrics:
            for line in self.lyrics:
                item = QListWidgetItem(
                    f"[{self._fmt_ms(line.time_ms)}] {line.text}"
                )
                item.setData(Qt.ItemDataRole.UserRole, line.time_ms)
                self.playlist.addItem(item)
        else:
            self.playlist.addItem("No timestamped lyrics found")

        self.player.setSource(QUrl.fromLocalFile(path))
        self.lyric_label.setText("♪ No lyrics ♪")
        self.current_idx = -1
        self.start_epoch = None

        self.rpc.update(
            details=f"{self.artist} — {self.title}",
            state="No lyrics available" if not self.lyrics else "Ready",
            start_epoch=None,
            large_image="music",
            large_text=self.album or self.title,
        )

        self._start_cover_upload(path)

    # ------------------------------------------------------------ playback

    def play(self):
        if self.current_file is None and self.queue:
            self.play_next()
            return
        self.player.play()
        self.start_epoch = int(time.time())
        self.timer.start()

    def play_next(self):
        if not self.queue:
            return

        if self.shuffle_enabled:
            if self.queue_index >= 0:
                self._shuffle_history.append(self.queue_index)

            self._shuffle_bag = [
                i for i in self._shuffle_bag if 0 <= i < len(self.queue)
            ]

            if not self._shuffle_bag:
                self._reset_shuffle_bag()

            if not self._shuffle_bag:
                self.queue_index = 0
            else:
                self.queue_index = self._shuffle_bag.pop(0)
        else:
            self.queue_index = (self.queue_index + 1) % len(self.queue)

        self.queue_list.setCurrentRow(self.queue_index)
        self._load_track(self.queue[self.queue_index])
        self.play()

    def play_prev(self):
        if not self.queue:
            return

        if self.player.position() > 3000:
            self.player.setPosition(0)
            return

        if self.shuffle_enabled and self._shuffle_history:
            self.queue_index = self._shuffle_history.pop()
        else:
            self.queue_index = (self.queue_index - 1) % len(self.queue)

        self.queue_list.setCurrentRow(self.queue_index)
        self._load_track(self.queue[self.queue_index])
        self.play()

    def stop(self, silent: bool = False):
        self.player.stop()
        self.timer.stop()
        self.start_epoch = None
        self.current_idx = -1
        self.lyric_label.setText("♪ No lyrics ♪")

        if not silent and self.current_file:
            self.rpc.update(
                details=f"{self.artist} — {self.title}",
                state="No lyrics available" if not self.lyrics else "Stopped",
                start_epoch=None,
                large_image=self.cover_url or "music",
                large_text=self.album or self.title,
            )

    # ------------------------------------------------------------ signals

    def _on_duration_changed(self, duration: int):
        self.position_slider.setRange(0, duration)

    def _on_position_changed(self, position: int):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat_song_enabled and self.current_file:
                self._repeat_current_track()
            else:
                self.play_next()

    def _on_queue_double_click(self, item: QListWidgetItem):
        row = self.queue_list.row(item)
        self._jump_to_queue_index(row)

    # ------------------------------------------------------------ tick

    def _on_tick(self):
        if not self.current_file:
            return

        pos = self.player.position()

        if self.lyrics:
            idx, line = current_line(self.lyrics, pos)

            if idx != self.current_idx:
                self.current_idx = idx

                if line:
                    self.lyric_label.setText(line.text)
                    self.playlist.setCurrentRow(idx)
                    self.playlist.scrollToItem(self.playlist.item(idx))
                else:
                    self.lyric_label.setText("♪ No lyrics ♪")

                state = line.text if line else "No lyrics available"
                self.rpc.update(
                    details=f"{self.artist} — {self.title}",
                    state=state,
                    start_epoch=self.start_epoch,
                    large_image=self.cover_url or "music",
                    large_text=self.album or self.title,
                )
        else:
            self.rpc.update(
                details=f"{self.artist} — {self.title}",
                state="No lyrics available",
                start_epoch=self.start_epoch,
                large_image=self.cover_url or "music",
                large_text=self.album or self.title,
            )

    def _on_playlist_click(self, item: QListWidgetItem):
        ms = item.data(Qt.ItemDataRole.UserRole)
        if ms is not None:
            self.player.setPosition(ms)

    def _fmt_ms(self, ms: int) -> str:
        seconds, ms = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes:02d}:{seconds:02d}.{ms // 10:02d}"

    def closeEvent(self, event):
        self._media_keys.stop()
        if self._cover_worker and self._cover_worker.isRunning():
            self._cover_worker.wait(2000)
        if self._scanner and self._scanner.isRunning():
            self._scanner.wait(2000)
        self.rpc.close()
        super().closeEvent(event)