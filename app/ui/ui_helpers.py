from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPaintEvent, QPen, QPixmap, QPainterPath, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def make_label(text: str, role: str | None = None, word_wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(word_wrap)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        label.setProperty("role", role)
    return label


def build_initials_avatar(name: str, size: int, *, shape: str = "circle") -> QPixmap:
    seed = (name or "A").strip()[:1].upper()
    palette = [
        "#3ECF8E",
        "#6FB4FF",
        "#E8B454",
        "#F26D6D",
        "#8B5CF6",
        "#22D3EE",
        "#F97316",
    ]
    color = palette[ord(seed[0]) % len(palette)] if seed else palette[0]
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))

    if shape == "circle":
        painter.drawEllipse(0, 0, size, size)
    else:
        painter.drawRoundedRect(0, 0, size, size, max(6, size // 6), max(6, size // 6))

    font = QFont("DM Sans")
    font.setBold(True)
    font.setPointSize(max(10, int(size * 0.45)))
    painter.setFont(font)
    painter.setPen(QColor("#09110D"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, seed)
    painter.end()
    return pixmap


def load_avatar_pixmap(path: str, size: int, *, shape: str = "circle") -> QPixmap | None:
    if not path:
        return None
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    pixmap = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    if shape in {"circle", "rounded"}:
        mask = QPixmap(size, size)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path_obj = QPainterPath()
        if shape == "circle":
            path_obj.addEllipse(0, 0, size, size)
        else:
            radius = max(6, size // 6)
            path_obj.addRoundedRect(0, 0, size, size, radius, radius)
        painter.fillPath(path_obj, QColor("#FFFFFF"))
        painter.end()
        pixmap.setMask(mask.mask())
    return pixmap


def make_button(text: str, kind: str = "secondary") -> QPushButton:
    button = QPushButton(text)
    button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    object_name = {
        "primary": "PrimaryButton",
        "secondary": "SecondaryButton",
        "ghost": "GhostButton",
        "soft": "SecondaryButton",
        "danger": "DangerButton",
    }.get(kind, "SecondaryButton")
    button.setObjectName(object_name)
    return button


def make_pill(text: str, tone: str = "default") -> QLabel:
    pill = QLabel(text)
    pill.setObjectName(
        {
            "default": "Pill",
            "accent": "PillAccent",
            "danger": "PillDanger",
            "good": "PillGood",
            "success": "PillGood",
        }.get(tone, "Pill")
    )
    pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return pill


def make_section_header(
    title: str,
    description: str = "",
    *,
    eyebrow: str | None = None,
    actions: Iterable[QWidget] | None = None,
) -> tuple[QWidget, QVBoxLayout]:
    host = QWidget()
    root = QHBoxLayout(host)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(12)

    left = QVBoxLayout()
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(4)
    if eyebrow:
        left.addWidget(make_label(eyebrow, "meta"))
    left.addWidget(make_label(title, "sectionTitle"))
    if description:
        left.addWidget(make_label(description, "muted", True))

    root.addLayout(left, 1)

    if actions:
        right = QHBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        for action in actions:
            right.addWidget(action)
        root.addLayout(right)

    return host, left


def make_card(
    title: str,
    description: str = "",
    *,
    elevated: bool = False,
    eyebrow: str | None = None,
) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("CardElevated" if elevated else "Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)

    if title or description or eyebrow:
        header, _ = make_section_header(title, description, eyebrow=eyebrow)
        layout.addWidget(header)

    return card, layout

def make_toolbar_card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("ToolbarCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(10)
    return frame, layout


def make_kpi_tile(label: str, value: str, delta: str = "", *, tone: str = "default") -> tuple[QFrame, QLabel, QLabel]:
    tile = QFrame()
    tile.setObjectName("Card")
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)

    label_widget = make_label(label, "meta")
    value_widget = make_label(value, "pageTitle")
    delta_tone = "accent" if tone == "good" else "danger" if tone == "danger" else "default"
    delta_widget = make_pill(delta or "—", delta_tone)
    delta_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    layout.addWidget(label_widget)
    layout.addWidget(value_widget)
    layout.addWidget(delta_widget, 0, Qt.AlignmentFlag.AlignLeft)
    return tile, value_widget, delta_widget


class StepperSpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.PlusMinus)
        self.setSingleStep(1)


class ToggleSwitch(QCheckBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setText("")
        self.setFixedSize(46, 28)

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        bg = QColor("#3ECF8E") if self.isChecked() else QColor("#162420")
        border = QColor("#3ECF8E") if self.isChecked() else QColor(255, 255, 255, 30)
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        knob_size = rect.height() - 6
        knob_x = rect.right() - knob_size - 3 if self.isChecked() else rect.left() + 3
        knob_rect = rect.adjusted(0, 3, 0, -3)
        knob_rect.setLeft(knob_x)
        knob_rect.setWidth(knob_size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ECF6F1"))
        painter.drawEllipse(knob_rect)
        painter.end()


class DetailOverlay(QFrame):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DetailOverlay")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)
        self.title_label = make_label(title, "sectionTitle")
        self.subtitle_label = make_label(subtitle, "muted", True)
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)

        self.close_button = QToolButton()
        self.close_button.setText("×")
        self.close_button.setObjectName("GhostButton")
        self.close_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_button.setFixedSize(36, 36)

        header.addLayout(title_col, 1)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.body_widget = QWidget()
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        root.addWidget(self.body_widget, 1)

        self.actions_widget = QWidget()
        self.actions_layout = QVBoxLayout(self.actions_widget)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(8)
        root.addWidget(self.actions_widget, 0)


class FadeScaleMixin:
    def animate_dialog_in(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        self._fade_animation = QPropertyAnimation(effect, b"opacity", widget)
        self._fade_animation.setDuration(180)
        self._fade_animation.setStartValue(0.0)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_animation.start()


class GalaxyBackdropWidget(QWidget):
    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(5, 12, 9, 220))

        glow = QColor("#3ECF8E")
        glow.setAlpha(28)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(self.width() * 0.65), int(self.height() * 0.18), 260, 180)
        painter.drawEllipse(int(self.width() * 0.18), int(self.height() * 0.56), 320, 220)
        painter.end()


class PopupBaseDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_offset = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class TemplateGuidePopup(PopupBaseDialog):
    def __init__(self, placeholders: list[dict[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Use CardElevated object name so the theme stylesheet applies a solid background
        self.setObjectName("CardElevated")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title_label = make_label("Sidebar Subtitle Placeholders", "sectionTitle")
        close_btn = make_button("×", "ghost")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close)
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(close_btn)
        layout.addLayout(header)

        intro = make_label(
            "Use these placeholders in your custom sidebar text. "
            "They will be replaced automatically with real-time values:",
            "muted",
            word_wrap=True
        )
        layout.addWidget(intro)

        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        table = QTableWidget(len(placeholders), 2)
        table.setHorizontalHeaderLabels(["Placeholder", "Description"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setMinimumHeight(min(280, 36 + len(placeholders) * 28))
        table.setMaximumHeight(380)

        for idx, item in enumerate(placeholders):
            p_item = QTableWidgetItem(item["placeholder"])
            table.setItem(idx, 0, p_item)

            desc_item = QTableWidgetItem(item["description"])
            table.setItem(idx, 1, desc_item)

        layout.addWidget(table)

        hint = make_label("Click and drag to move. Click × to close.", "muted")
        layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignCenter)

        # Let the layout size the window naturally; just set a sensible minimum
        self.setMinimumSize(480, 340)

    def show_at_widget(self, target: QWidget) -> None:
        self.adjustSize()
        # Map the bottom-left of the target widget to global screen coordinates
        gp = target.mapToGlobal(target.rect().bottomLeft())
        x = gp.x()
        y = gp.y() + 4  # small gap below the widget

        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(gp)
        if screen is None:
            screen = QApplication.primaryScreen()
        avail = screen.availableGeometry()

        # Clamp within screen bounds
        if x + self.width() > avail.right():
            x = avail.right() - self.width()
        if y + self.height() > avail.bottom():
            # flip above the widget
            y = target.mapToGlobal(target.rect().topLeft()).y() - self.height() - 4
        x = max(avail.left(), x)
        y = max(avail.top(), y)

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

