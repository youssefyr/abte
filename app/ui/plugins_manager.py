from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QListWidget, QPushButton, QTextEdit, QVBoxLayout, QWidget


class PluginsManagerWidget(QWidget):
    def __init__(self, plugin_manager, parent=None) -> None:
        super().__init__(parent)
        self._manager = plugin_manager
        layout = QHBoxLayout(self)
        self.list = QListWidget()
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.name_label = QLabel("Plugin")
        self.id_label = QLabel("ID")
        self.version_label = QLabel("Version")
        self.enabled = QCheckBox("Enabled")
        self.description = QTextEdit()
        self.description.setReadOnly(True)
        self.reload_btn = QPushButton("Reload plugins")
        detail_layout.addWidget(self.name_label)
        detail_layout.addWidget(self.id_label)
        detail_layout.addWidget(self.version_label)
        detail_layout.addWidget(self.enabled)
        detail_layout.addWidget(self.description)
        detail_layout.addWidget(self.reload_btn)
        layout.addWidget(self.list, 1)
        layout.addWidget(detail, 2)
        self.reload_btn.clicked.connect(self._reload)
        self.list.currentRowChanged.connect(self._on_selected)
        self.enabled.toggled.connect(self._toggle_enabled)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for plugin in self._manager.plugins():
            self.list.addItem(plugin.name)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_selected(self, row: int) -> None:
        plugins = self._manager.plugins()
        if row < 0 or row >= len(plugins):
            return
        p = plugins[row]
        self.name_label.setText(p.name)
        self.id_label.setText(p.id)
        self.version_label.setText(p.version)
        self.enabled.blockSignals(True)
        self.enabled.setChecked(p.enabled)
        self.enabled.blockSignals(False)
        self.description.setPlainText(p.description)

    def _toggle_enabled(self, checked: bool) -> None:
        row = self.list.currentRow()
        plugins = self._manager.plugins()
        if row < 0 or row >= len(plugins):
            return
        plugin = plugins[row]
        if checked:
            self._manager.enable(plugin.id)
        else:
            self._manager.disable(plugin.id)
