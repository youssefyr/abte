"""
app/ui/slm_model_selector.py

Reusable widget for browsing, downloading, and selecting local GGUF models.
Can be embedded in both the startup wizard and the settings page.

Responsibilities:
- Shows the known model catalog with download status
- Downloads a selected model in a background thread with live progress
- Emits model_path_changed(str) when the user activates a model
- Never deletes existing model files — only changes which path is active
"""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.slm.model_catalog import KNOWN_MODELS, ModelEntry, find_downloaded_model, detect_entry_for_path
from app.ui.ui_helpers import make_button, make_label


# ---------------------------------------------------------------------------
# Download worker
# ---------------------------------------------------------------------------

class _DownloadThread(QThread):
    progress = Signal(int)        # 0-100
    finished = Signal(bool, str)  # success, error_or_empty

    def __init__(self, url: str, dest: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest = dest

    def run(self) -> None:
        try:
            self._dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._dest.with_suffix(".tmp")

            def hook(block: int, block_size: int, total: int) -> None:
                if total > 0:
                    self.progress.emit(min(100, int(block * block_size * 100 / total)))

            urllib.request.urlretrieve(self._url, str(tmp), reporthook=hook)
            tmp.replace(self._dest)
            self.finished.emit(True, "")
        except Exception as exc:
            try:
                tmp = self._dest.with_suffix(".tmp")
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            self.finished.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class ModelSelectorWidget(QFrame):
    """
    Embeddable model browser / downloader / selector.

    Signals
    -------
    model_path_changed(str)  — emitted when the user clicks "Use this model"
                               and the model file exists on disk.
    """

    model_path_changed = Signal(str)

    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._download_thread: _DownloadThread | None = None
        self._active_row: int = -1

        self.setObjectName("Card")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Header
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        title = make_label("Available Models", "sectionTitle")
        
        self._open_dir_btn = make_button("Open folder", "ghost")
        self._open_dir_btn.setFixedHeight(28)
        self._open_dir_btn.clicked.connect(self._open_models_dir)

        self._custom_btn = make_button("Load custom file...", "ghost")
        self._custom_btn.setFixedHeight(28)
        self._custom_btn.clicked.connect(self._on_custom_model_requested)

        self._refresh_btn = make_button("Refresh status", "ghost")
        self._refresh_btn.setFixedHeight(28)
        self._refresh_btn.clicked.connect(self._refresh_table)
        
        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(self._open_dir_btn)
        header_row.addWidget(self._custom_btn)
        header_row.addWidget(self._refresh_btn)
        root.addLayout(header_row)

        # Table: Name | ~Size | RAM | Status | [Download] [Use]
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Model", "~Size", "RAM req.", "Status", "Action"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(4, 180)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setMinimumHeight(80)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root.addWidget(self._table)

        # Progress + status
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(6)
        root.addWidget(self._progress)

        self._status_label = make_label("", "muted", word_wrap=True)
        self._status_label.setVisible(False)
        root.addWidget(self._status_label)

        # Active model indicator
        self._active_label = make_label("Active model: —", "muted", word_wrap=True)
        root.addWidget(self._active_label)

        self._populate_table()
        self._refresh_table()
        self._update_active_label()

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _models_dir(self) -> Path:
        return Path(self._settings.app_data_dir()) / "models"

    def _open_models_dir(self) -> None:
        p = self._models_dir()
        try:
            p.mkdir(parents=True, exist_ok=True)
            abs_path = str(p.resolve())
            QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path))
        except Exception as exc:
            self._status_label.setVisible(True)
            self._status_label.setText(f"Failed to open directory: {exc}")

    def _on_custom_model_requested(self) -> None:
        default_dir = str(self._models_dir().resolve())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select GGUF Model File",
            default_dir,
            "GGUF Models (*.gguf);;All files (*)",
        )
        if not path:
            return

        p = Path(path).resolve()
        if not p.exists():
            QMessageBox.warning(self, "Invalid file", f"The selected file does not exist:\n{path}")
            return

        if p.suffix.lower() != ".gguf":
            reply = QMessageBox.question(
                self,
                "Non-GGUF file extension",
                f"The selected file extension ({p.suffix}) is not '.gguf'. GGUF is the recommended format for local SLMs.\n\nDo you want to use this model anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        path_str = str(p)
        self._settings.set("SLM/model_path", path_str)
        self._settings.sync()
        self._refresh_table()
        self._status_label.setVisible(True)
        self._status_label.setText(f"Active model set to custom path: {p.name}")
        self.model_path_changed.emit(path_str)

    def _model_path(self, entry: ModelEntry) -> Path:
        found = find_downloaded_model(entry, Path(self._settings.app_data_dir()))
        if found:
            return found
        return self._models_dir() / entry.filename

    def _is_downloaded(self, entry: ModelEntry) -> bool:
        p = self._model_path(entry)
        try:
            return p.exists() and p.stat().st_size > 0
        except Exception:
            return False

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        for row, entry in enumerate(KNOWN_MODELS):
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(entry.name))
            self._table.setItem(row, 1, QTableWidgetItem(f"{entry.size_mb_approx} MB"))
            self._table.setItem(row, 2, QTableWidgetItem(f"{entry.ram_required_mb} MB"))

            status_item = QTableWidgetItem("Checking…")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, status_item)

            action_cell = _ActionCell(entry, row, self)
            action_cell.download_requested.connect(self._on_download_requested)
            action_cell.use_requested.connect(self._on_use_requested)
            self._table.setCellWidget(row, 4, action_cell)

        self._table.resizeRowsToContents()

    def _is_active(self, entry: ModelEntry) -> bool:
        current_val = self._settings.get("SLM/model_path", "")
        if not current_val:
            return False
        current = str(current_val)
        try:
            p_current = Path(current).resolve()
            p_entry = self._model_path(entry).resolve()
            return p_current == p_entry
        except Exception:
            return current == str(self._model_path(entry))

    def _refresh_table(self) -> None:
        for row, entry in enumerate(KNOWN_MODELS):
            downloaded = self._is_downloaded(entry)
            active = self._is_active(entry)
            status_text = "✓ Active" if (downloaded and active) else ("✓ Ready" if downloaded else "Not downloaded")
            item = self._table.item(row, 3)
            if item:
                item.setText(status_text)

            cell = self._table.cellWidget(row, 4)
            if isinstance(cell, _ActionCell):
                cell.set_state(downloaded, active)
                cell.set_downloading(False)

        self._update_active_label()

    def _update_active_label(self) -> None:
        current = str(self._settings.get("SLM/model_path", "") or "")
        if current:
            matched_name = "Custom / Unknown Model"
            entry = detect_entry_for_path(Path(current))
            if entry:
                matched_name = entry.name
            self._active_label.setText(f"Active model: {matched_name} ({current})")
        else:
            self._active_label.setText("Active model: none configured")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_download_requested(self, entry: ModelEntry, row: int) -> None:
        if self._download_thread and self._download_thread.isRunning():
            return  # already downloading something

        dest = self._model_path(entry)
        self._active_row = row

        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_label.setVisible(True)
        self._status_label.setText(f"Downloading {entry.name}…")

        cell = self._table.cellWidget(row, 4)
        if isinstance(cell, _ActionCell):
            cell.set_downloading(True)

        status_item = self._table.item(row, 3)
        if status_item:
            status_item.setText("Downloading…")

        self._download_thread = _DownloadThread(entry.download_url, dest, self)
        self._download_thread.progress.connect(self._progress.setValue)
        self._download_thread.finished.connect(lambda ok, err: self._on_download_done(ok, err, row))
        self._download_thread.start()

    def _on_download_done(self, success: bool, error: str, row: int) -> None:
        self._progress.setVisible(False)
        self._active_row = -1
        if success:
            self._status_label.setText("Download complete.")
            entry = KNOWN_MODELS[row]
            active = self._is_active(entry)
            status_item = self._table.item(row, 3)
            if status_item:
                status_item.setText("✓ Active" if active else "✓ Ready")
            cell = self._table.cellWidget(row, 4)
            if isinstance(cell, _ActionCell):
                cell.set_state(True, active)
                cell.set_downloading(False)
        else:
            self._status_label.setText(f"Download failed: {error}")
            status_item = self._table.item(row, 3)
            if status_item:
                status_item.setText("Failed")
            cell = self._table.cellWidget(row, 4)
            if isinstance(cell, _ActionCell):
                cell.set_state(False, False)
                cell.set_downloading(False)

    def _on_use_requested(self, entry: ModelEntry, row: int) -> None:
        path = self._model_path(entry)
        self._settings.set("SLM/model_path", str(path))
        self._settings.sync()
        self._refresh_table()
        self._status_label.setVisible(True)
        self._status_label.setText(f"Active model set to: {entry.name}")
        self.model_path_changed.emit(str(path))

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def current_model_path(self) -> str:
        return str(self._settings.get("SLM/model_path", "") or "")

    def refresh(self) -> None:
        self._refresh_table()


# ---------------------------------------------------------------------------
# Per-row action cell widget
# ---------------------------------------------------------------------------

class _ActionCell(QWidget):
    download_requested = Signal(object, int)  # ModelEntry, row
    use_requested = Signal(object, int)

    def __init__(self, entry: ModelEntry, row: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._row = row

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self._dl_btn = make_button("Download", "ghost")
        self._dl_btn.setFixedHeight(26)
        self._dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dl_btn.clicked.connect(lambda: self.download_requested.emit(self._entry, self._row))

        self._use_btn = make_button("Use", "secondary")
        self._use_btn.setFixedHeight(26)
        self._use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_btn.clicked.connect(lambda: self.use_requested.emit(self._entry, self._row))
        self._use_btn.setVisible(False)

        layout.addWidget(self._dl_btn)
        layout.addWidget(self._use_btn)

    def set_state(self, downloaded: bool, active: bool) -> None:
        if not downloaded:
            self._dl_btn.setVisible(True)
            self._use_btn.setVisible(False)
        else:
            self._dl_btn.setVisible(False)
            self._use_btn.setVisible(True)
            if active:
                self._use_btn.setEnabled(False)
                self._use_btn.setText("Active")
            else:
                self._use_btn.setEnabled(True)
                self._use_btn.setText("Use")

    def set_downloaded(self, downloaded: bool) -> None:
        self.set_state(downloaded, False)

    def set_downloading(self, active: bool) -> None:
        self._dl_btn.setEnabled(not active)
        self._dl_btn.setText("Downloading…" if active else "Download")
        if active:
            self._use_btn.setVisible(False)
