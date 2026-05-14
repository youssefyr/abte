from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImageReader, QPainter, QPainterPath, QPen, QPixmap, QColor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from app.ui.ui_helpers import FadeScaleMixin, make_button, make_label


class AvatarCropper(QWidget):
    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._original = pixmap
        self._display = QPixmap()
        self._display_rect = QRect()
        self._selection = QRect()
        self._drag_state: str | None = None
        self._drag_origin = QPoint()
        self._orig_selection = QRect()
        self.setMouseTracking(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._display = self._original.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - self._display.width()) // 2
        y = (self.height() - self._display.height()) // 2
        self._display_rect = QRect(x, y, self._display.width(), self._display.height())
        
        if self._selection.isNull():
            side = int(min(self._display.width(), self._display.height()) * 0.7)
            sx = self._display_rect.x() + (self._display.width() - side) // 2
            sy = self._display_rect.y() + (self._display.height() - side) // 2
            self._selection = QRect(sx, sy, side, side)
        else:
            # Re-center selection if it goes out of bounds on resize
            if not self._display_rect.contains(self._selection):
                side = int(min(self._display.width(), self._display.height()) * 0.7)
                sx = self._display_rect.x() + (self._display.width() - side) // 2
                sy = self._display_rect.y() + (self._display.height() - side) // 2
                self._selection = QRect(sx, sy, side, side)
        self.update()

    def _hit_test(self, pos: QPoint) -> str | None:
        hs = 16  # Slightly larger hit area
        r = self._selection
        tl = QRect(r.x() - hs // 2, r.y() - hs // 2, hs, hs)
        tr = QRect(r.right() - hs // 2, r.y() - hs // 2, hs, hs)
        bl = QRect(r.x() - hs // 2, r.bottom() - hs // 2, hs, hs)
        br = QRect(r.right() - hs // 2, r.bottom() - hs // 2, hs, hs)
        
        if tl.contains(pos): return "top_left"
        if tr.contains(pos): return "top_right"
        if bl.contains(pos): return "bottom_left"
        if br.contains(pos): return "bottom_right"
        if r.contains(pos): return "move"
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_state = self._hit_test(event.position().toPoint())
            if self._drag_state:
                self._drag_origin = event.position().toPoint()
                self._orig_selection = QRect(self._selection)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        
        if not self._drag_state:
            hit = self._hit_test(pos)
            if hit in ("top_left", "bottom_right"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ("top_right", "bottom_left"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        dx = pos.x() - self._drag_origin.x()
        dy = pos.y() - self._drag_origin.y()
        r = QRect(self._orig_selection)

        if self._drag_state == "move":
            r.translate(dx, dy)
            if r.left() < self._display_rect.left(): r.moveLeft(self._display_rect.left())
            if r.right() > self._display_rect.right(): r.moveRight(self._display_rect.right())
            if r.top() < self._display_rect.top(): r.moveTop(self._display_rect.top())
            if r.bottom() > self._display_rect.bottom(): r.moveBottom(self._display_rect.bottom())
            self._selection = r
        else:
            if self._drag_state == "bottom_right":
                diff = max(dx, dy)
                r.setWidth(r.width() + diff)
                r.setHeight(r.height() + diff)
            elif self._drag_state == "bottom_left":
                diff = max(-dx, dy)
                r.setLeft(r.left() - diff)
                r.setHeight(r.height() + diff)
            elif self._drag_state == "top_right":
                diff = max(dx, -dy)
                r.setWidth(r.width() + diff)
                r.setTop(r.top() - diff)
            elif self._drag_state == "top_left":
                diff = max(-dx, -dy)
                r.setLeft(r.left() - diff)
                r.setTop(r.top() - diff)
                
            side = min(r.width(), r.height())
            side = max(side, 50)
            
            if self._drag_state == "bottom_right":
                side = min(side, self._display_rect.right() - self._orig_selection.left())
                side = min(side, self._display_rect.bottom() - self._orig_selection.top())
                self._selection = QRect(self._orig_selection.left(), self._orig_selection.top(), side, side)
            elif self._drag_state == "bottom_left":
                side = min(side, self._orig_selection.right() - self._display_rect.left() + 1)
                side = min(side, self._display_rect.bottom() - self._orig_selection.top())
                self._selection = QRect(self._orig_selection.right() - side + 1, self._orig_selection.top(), side, side)
            elif self._drag_state == "top_right":
                side = min(side, self._display_rect.right() - self._orig_selection.left())
                side = min(side, self._orig_selection.bottom() - self._display_rect.top() + 1)
                self._selection = QRect(self._orig_selection.left(), self._orig_selection.bottom() - side + 1, side, side)
            elif self._drag_state == "top_left":
                side = min(side, self._orig_selection.right() - self._display_rect.left() + 1)
                side = min(side, self._orig_selection.bottom() - self._display_rect.top() + 1)
                self._selection = QRect(self._orig_selection.right() - side + 1, self._orig_selection.bottom() - side + 1, side, side)

        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_state = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        
        if not self._display.isNull():
            painter.drawPixmap(self._display_rect.topLeft(), self._display)
            
        overlay_path = QPainterPath()
        overlay_path.addRect(self.rect())
        selection_path = QPainterPath()
        selection_path.addRect(self._selection)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawPath(overlay_path.subtracted(selection_path))
        
        pen = QPen(Qt.GlobalColor.white)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._selection)
        
        hs = 12
        painter.setBrush(Qt.GlobalColor.white)
        painter.setPen(Qt.PenStyle.NoPen)
        r = self._selection
        painter.drawRect(r.x() - hs // 2, r.y() - hs // 2, hs, hs)
        painter.drawRect(r.right() - hs // 2, r.y() - hs // 2, hs, hs)
        painter.drawRect(r.x() - hs // 2, r.bottom() - hs // 2, hs, hs)
        painter.drawRect(r.right() - hs // 2, r.bottom() - hs // 2, hs, hs)
        
        painter.end()

    def cropped_pixmap(self) -> QPixmap:
        if self._display_rect.isNull() or self._selection.isNull():
            return self._original
        scale_x = self._original.width() / max(1, self._display_rect.width())
        scale_y = self._original.height() / max(1, self._display_rect.height())
        sel = self._selection.intersected(self._display_rect)
        x = int((sel.x() - self._display_rect.x()) * scale_x)
        y = int((sel.y() - self._display_rect.y()) * scale_y)
        side = int(min(sel.width() * scale_x, sel.height() * scale_y))
        side = max(1, side)
        return self._original.copy(x, y, side, side)


class AvatarCropDialog(QDialog, FadeScaleMixin):
    def __init__(self, image_path: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crop profile photo")
        self.setModal(True)
        self.setFixedSize(540, 600)
        
        reader = QImageReader(str(image_path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise ValueError("Failed to load image")
        pixmap = QPixmap.fromImage(image)

        self._cropper = AvatarCropper(pixmap, self)
        
        cancel_btn = make_button("Cancel", "ghost")
        cancel_btn.clicked.connect(self.reject)
        
        save_btn = make_button("Save", "primary")
        save_btn.clicked.connect(self.accept)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        header = make_label("Crop profile photo", "dialogTitle")
        
        layout.addWidget(header)
        layout.addWidget(make_label("Drag the corners to adjust the selection.", "muted"))
        layout.addWidget(self._cropper, 1)
        layout.addLayout(btn_layout)
        
        self.animate_dialog_in(self)

    def cropped_pixmap(self) -> QPixmap:
        return self._cropper.cropped_pixmap()
