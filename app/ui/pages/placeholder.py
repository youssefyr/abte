from PySide6.QtWidgets import QWidget, QVBoxLayout
from app.ui.pages.base_page import BasePage
from app.ui.ui_helpers import make_card, make_label

class PlaceholderPage(BasePage):
    def __init__(self, title, subtitle, metrics, parent=None):
        super().__init__(metrics, parent)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        card, layout = make_card(title, subtitle, elevated=False)
        lbl = make_label("Construction area.\nMore robust UI modules to land here.", "muted", True)
        self.lbl = lbl
        layout.addWidget(lbl)
        layout.addStretch()

        self.main_layout.addWidget(card)
        
    def filter_content(self, text: str):
        if not text:
            self.lbl.setStyleSheet("")
            return
            
        if text.lower() in self.lbl.text().lower():
            self.lbl.setStyleSheet("background-color: yellow; color: black;")
        else:
            self.lbl.setStyleSheet("")