from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, pi, sin
from random import Random

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget


# =========================
# Animation constants
# =========================
BACKGROUND_RGBA = (2, 6, 23, 89)  # ~35% alpha
ROTATION_RADIANS = pi / 8         # 22.5°
ROTATION_DEGREES = 22.5
TILT_FACTOR = 0.35
FRAME_INTERVAL_MS = 16

PARTICLE_COUNT_DESKTOP = 3200
PARTICLE_COUNT_COMPACT = 1800
GALAXY_RADIUS_FACTOR = 0.82
PARTICLE_RADIUS_POWER = 3.0
PARTICLE_SPEED_MIN = 0.0001
PARTICLE_SPEED_SPREAD = 0.0008
PARTICLE_SIZE_MIN = 0.5
PARTICLE_SIZE_SPREAD = 1.5
PARTICLE_YOFFSET_SPAN = 120.0
PARTICLE_DEPTH_SCALE = 0.4

CORE_GLOW_RADIUS_FACTOR = 0.40
CORE_GLOW_WHITE = QColor(255, 255, 255, 31)
CORE_GLOW_CYAN = QColor(120, 180, 255, 10)
CORE_GLOW_EDGE = QColor(0, 0, 0, 0)

COMET_SPAWN_CHANCE = 0.08
COMET_MAX_COUNT = 40
COMET_SPEED_MIN = 3.5
COMET_SPEED_SPREAD = 3.5
COMET_MAX_LIFE_MIN = 80
COMET_MAX_LIFE_SPREAD = 120
COMET_TURN_DISTANCE = 35.0
COMET_TURN_CHANCE = 0.08
COMET_TAIL_LENGTH = 60
COMET_LINE_WIDTH = 2.5
COMET_HEAD_RADIUS = 0.0
COMET_HEAD_GLOW_RADIUS = 0.0

VIA_ENABLED = True
VIA_RADIUS = 1.6
VIA_HOLE_RADIUS = 0.7
VIA_HOLE_COLOR = QColor(2, 6, 23, 255)

COMET_COLORS = [
    QColor("#0ea5e9"),
    QColor("#8b5cf6"),
    QColor("#34d399"),
    QColor("#fbbf24"),
    QColor("#e879f9"),
]


@dataclass(slots=True)
class GalaxyParticle:
    angle: float
    radius: float
    speed: float
    base_size: float
    color: QColor
    y_offset: float


@dataclass(slots=True)
class TracePoint:
    x: float
    y: float
    is_turn: bool = False


@dataclass(slots=True)
class CircuitComet:
    x: float
    y: float
    angle: float
    color: QColor
    speed: float
    max_life: int
    life: int = 0
    distance_since_turn: float = 0.0
    history: list[TracePoint] = field(default_factory=list)


@dataclass(slots=True)
class FrameState:
    width: int
    height: int
    particles: list[GalaxyParticle]
    comets: list[CircuitComet]


class FadeScaleMixin:
    def animate_dialog_in(self, widget: QWidget) -> None:
        end_geo = widget.geometry()
        start_geo = QRect(end_geo.x(), end_geo.y() + 18, end_geo.width(), end_geo.height())
        widget.setGeometry(start_geo)

        self._geo_anim = QPropertyAnimation(widget, b"geometry", widget)
        self._geo_anim.setDuration(240)
        self._geo_anim.setStartValue(start_geo)
        self._geo_anim.setEndValue(end_geo)
        self._geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._geo_anim.start()


class GalaxySimulationWorker(QObject):
    frame_ready = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._rng = Random(37)
        self._width = 1280
        self._height = 720
        self._max_radius = 700.0
        self._particles: list[GalaxyParticle] = []
        self._comets: list[CircuitComet] = []
        self._timer: QTimer | None = None

    def start(self) -> None:
        self._rebuild_scene()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._step)
        self._timer.start()

    def resize_scene(self, width: int, height: int) -> None:
        self._width = max(1, width)
        self._height = max(1, height)
        self._max_radius = max(self._width, self._height) * GALAXY_RADIUS_FACTOR
        self._rebuild_particles()

    def _rebuild_scene(self) -> None:
        self._max_radius = max(self._width, self._height) * GALAXY_RADIUS_FACTOR
        self._rebuild_particles()
        self._comets.clear()

    def _rebuild_particles(self) -> None:
        count = PARTICLE_COUNT_DESKTOP if self._width > 900 else PARTICLE_COUNT_COMPACT
        self._particles = [self._make_particle() for _ in range(count)]

    def _make_particle(self) -> GalaxyParticle:
        angle = self._rng.random() * pi * 2
        radius = (self._rng.random() ** PARTICLE_RADIUS_POWER) * self._max_radius
        speed = (PARTICLE_SPEED_MIN + self._rng.random() * PARTICLE_SPEED_SPREAD) * (
            self._max_radius / (radius + 50.0)
        )
        base_size = self._rng.random() * PARTICLE_SIZE_SPREAD + PARTICLE_SIZE_MIN

        thickness_mod = max(0.0, 1.0 - (radius / self._max_radius))
        y_offset = (self._rng.random() - 0.5) * PARTICLE_YOFFSET_SPAN * (thickness_mod ** 2)

        ratio = radius / self._max_radius
        r = int(100 + 155 * (1 - ratio))
        g = int(150 + 105 * (1 - ratio))
        b = 255
        a = max(26, int((0.9 - ratio * 0.5) * 255))
        color = QColor(r, g, b, a)

        return GalaxyParticle(
            angle=angle,
            radius=radius,
            speed=speed,
            base_size=base_size,
            color=color,
            y_offset=y_offset,
        )

    def _spawn_comet(self) -> None:
        if len(self._comets) >= COMET_MAX_COUNT:
            return

        cx = self._width / 2.0
        cy = self._height / 2.0
        spawn_angle = self._rng.random() * pi * 2
        spawn_dist = 40.0 + self._rng.random() * (min(self._width, self._height) * 0.45)

        x = cx + cos(spawn_angle) * spawn_dist
        y = cy + sin(spawn_angle) * spawn_dist

        raw_angle = atan2(y - cy, x - cx)
        snapped_angle = round(raw_angle / (pi / 4)) * (pi / 4)

        speed = COMET_SPEED_MIN + self._rng.random() * COMET_SPEED_SPREAD
        max_life = int(COMET_MAX_LIFE_MIN + self._rng.random() * COMET_MAX_LIFE_SPREAD)
        color = COMET_COLORS[self._rng.randrange(len(COMET_COLORS))]

        self._comets.append(
            CircuitComet(
                x=x,
                y=y,
                angle=snapped_angle,
                color=QColor(color),
                speed=speed,
                max_life=max_life,
            )
        )

    def _step(self) -> None:
        for particle in self._particles:
            particle.angle += particle.speed

        if self._rng.random() < COMET_SPAWN_CHANCE:
            self._spawn_comet()

        next_comets: list[CircuitComet] = []
        for comet in self._comets:
            is_turn = False
            if comet.distance_since_turn > COMET_TURN_DISTANCE and self._rng.random() < COMET_TURN_CHANCE:
                comet.angle += (pi / 4) if self._rng.random() > 0.5 else -(pi / 4)
                comet.distance_since_turn = 0.0
                is_turn = True

            comet.history.append(TracePoint(comet.x, comet.y, is_turn=is_turn))
            if len(comet.history) > COMET_TAIL_LENGTH:
                comet.history.pop(0)

            dx = cos(comet.angle) * comet.speed
            dy = sin(comet.angle) * comet.speed
            comet.x += dx
            comet.y += dy
            comet.distance_since_turn += comet.speed
            comet.life += 1

            if comet.life < comet.max_life:
                next_comets.append(comet)

        self._comets = next_comets
        self.frame_ready.emit(
            FrameState(
                width=self._width,
                height=self._height,
                particles=list(self._particles),
                comets=list(self._comets),
            )
        )


class GalaxyBackdropWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._frame = FrameState(1, 1, [], [])
        self._worker_thread = QThread(self)
        self._worker = GalaxySimulationWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.start)
        self._worker.frame_ready.connect(self._on_frame_ready, Qt.ConnectionType.QueuedConnection)
        self._worker_thread.start()

    def _on_frame_ready(self, frame: FrameState) -> None:
        self._frame = frame
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.width()
        height = self.height()
        QTimer.singleShot(0, lambda: self._worker.resize_scene(width, height))

    def closeEvent(self, event) -> None:
        self._worker_thread.quit()
        self._worker_thread.wait(1000)
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        del event

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(*BACKGROUND_RGBA))

        width = self.width()
        height = self.height()
        cx = width / 2.0
        cy = height / 2.0
        max_radius = max(width, height) * GALAXY_RADIUS_FACTOR
        cos_rot = cos(ROTATION_RADIANS)
        sin_rot = sin(ROTATION_RADIANS)

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(ROTATION_DEGREES)
        painter.scale(1.0, TILT_FACTOR)

        glow = QRadialGradient(QPointF(0.0, 0.0), max_radius * CORE_GLOW_RADIUS_FACTOR)
        glow.setColorAt(0.0, CORE_GLOW_WHITE)
        glow.setColorAt(0.15, CORE_GLOW_CYAN)
        glow.setColorAt(1.0, CORE_GLOW_EDGE)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawRect(QRectF(-max_radius, -max_radius, max_radius * 2, max_radius * 2))
        painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        for particle in self._frame.particles:
            flat_x = cos(particle.angle) * particle.radius
            flat_y = sin(particle.angle) * particle.radius

            x_rot = flat_x * cos_rot - flat_y * sin_rot
            z_rot = flat_x * sin_rot + flat_y * cos_rot

            screen_x = cx + x_rot
            screen_y = cy + z_rot * TILT_FACTOR + particle.y_offset

            depth = z_rot / max_radius
            scale = 1.0 + depth * PARTICLE_DEPTH_SCALE
            final_size = max(0.1, particle.base_size * scale)

            painter.fillRect(
                QRectF(
                    screen_x - final_size / 2,
                    screen_y - final_size / 2,
                    final_size,
                    final_size,
                ),
                particle.color,
            )

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        for comet in self._frame.comets:
            current_life_alpha = max(0.0, 1.0 - (comet.life / comet.max_life))
            if current_life_alpha <= 0.0:
                continue

            painter.setBrush(Qt.BrushStyle.NoBrush)

            for i in range(len(comet.history) - 1):
                pt1 = comet.history[i]
                pt2 = comet.history[i + 1]
                fade = i / max(1, len(comet.history) - 1)

                color = QColor(comet.color)
                color.setAlphaF(fade * current_life_alpha)

                pen = QPen(
                    color,
                    COMET_LINE_WIDTH,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
                painter.setPen(pen)
                painter.drawLine(QPointF(pt1.x, pt1.y), QPointF(pt2.x, pt2.y))

                if VIA_ENABLED and pt2.is_turn:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(color)
                    painter.drawEllipse(QPointF(pt2.x, pt2.y), VIA_RADIUS, VIA_RADIUS)
                    painter.setBrush(VIA_HOLE_COLOR)
                    painter.drawEllipse(QPointF(pt2.x, pt2.y), VIA_HOLE_RADIUS, VIA_HOLE_RADIUS)

            if comet.history:
                last_pt = comet.history[-1]
                head_color = QColor(comet.color)
                head_color.setAlphaF(current_life_alpha)
                painter.setPen(
                    QPen(
                        head_color,
                        COMET_LINE_WIDTH,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                )
                painter.drawLine(QPointF(last_pt.x, last_pt.y), QPointF(comet.x, comet.y))