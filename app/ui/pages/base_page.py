from PySide6.QtWidgets import QWidget, QVBoxLayout

class BasePage(QWidget):
    def __init__(self, metrics, parent=None):
        super().__init__(parent)
        self.metrics = metrics
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(
            metrics.page_margin,
            metrics.page_margin,
            metrics.page_margin,
            metrics.page_margin
        )
        self.main_layout.setSpacing(metrics.card_gap)

    def apply_metrics(self, metrics):
        self.metrics = metrics
        self.main_layout.setContentsMargins(
            metrics.page_margin,
            metrics.page_margin,
            metrics.page_margin,
            metrics.page_margin
        )
        self.main_layout.setSpacing(metrics.card_gap)
        
    def filter_content(self, text: str):
        """Implement filtering based on search text"""
        pass