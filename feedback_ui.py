# Interactive Feedback MCP UI
# Developed by Fábio Ferreira (https://x.com/fabiomlferreira)
# Inspired by/related to dotcursorrules.com (https://dotcursorrules.com/)
# Enhanced by Pau Oliva (https://x.com/pof) with ideas from https://github.com/ttommyth/interactive-mcp
import os
import sys
import json
import argparse
import tempfile
import math
import re
import threading
import urllib.request
from urllib.parse import urlparse, unquote
from typing import Optional, TypedDict, List

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QTextEdit,
    QFrame, QFileDialog, QScrollArea, QSizePolicy, QDialog, QButtonGroup, QInputDialog, QGridLayout, QToolTip, QColorDialog
)
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QSettings, QSize, QPoint, QRect, QTimer
from PySide6.QtGui import QIcon, QKeyEvent, QPalette, QColor, QPixmap, QPainter, QPen, QGuiApplication, QKeySequence, QShortcut, QCursor, QPainterPath

_IMAGE_FILTERS = "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All Files (*)"
_THUMB_MAX = 120
_SHOT_PREFIX = "interactive_feedback_shot_"
_CLIP_PREFIX = "interactive_feedback_clip_"
_REMOTE_IMAGE_TIMEOUT_SEC = float(os.getenv("INTERACTIVE_FEEDBACK_REMOTE_IMAGE_TIMEOUT_SEC", "5"))
_REMOTE_IMAGE_MAX_BYTES = int(os.getenv("INTERACTIVE_FEEDBACK_REMOTE_IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))


def _resolve_feedback_icon() -> QIcon:
    """Resolve feedback icon from common local locations."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    image_exts = {".png", ".ico", ".icns", ".jpg", ".jpeg", ".webp", ".bmp"}
    env_icon = os.getenv("INTERACTIVE_FEEDBACK_ICON", "").strip()
    candidates = []
    if env_icon:
        candidates.append(env_icon)
    candidates.extend(
        [
            os.path.join(script_dir, "images", "feedback.png"),
            os.path.join(script_dir, "images", "icon.png"),
            os.path.join(script_dir, "images", "app.png"),
            os.path.join(script_dir, "images", "logo.png"),
            os.path.join(script_dir, "feedback.png"),
            os.path.join(script_dir, "icon.png"),
            os.path.join(script_dir, "app.png"),
            os.path.join(script_dir, "logo.png"),
        ]
    )
    images_dir = os.path.join(script_dir, "images")
    if os.path.isdir(images_dir):
        for name in sorted(os.listdir(images_dir)):
            ext = os.path.splitext(name.lower())[1]
            if ext in image_exts:
                candidates.append(os.path.join(images_dir, name))
    for icon_path in candidates:
        if os.path.isfile(icon_path):
            return QIcon(icon_path)
    return QIcon()


def _apply_app_identity(app: QApplication, app_icon: QIcon) -> None:
    """Apply app-level metadata so window title/icon are less platform-dependent."""
    app_name = "Interactive Feedback MCP"
    app.setApplicationName(app_name)
    app.setApplicationDisplayName(app_name)
    app.setOrganizationName("InteractiveFeedbackMCP")
    app.setDesktopFileName("interactive-feedback-mcp")
    if not app_icon.isNull():
        app.setWindowIcon(_optimize_icon_for_platform(app_icon))


def _effective_feedback_icon() -> QIcon:
    """Prefer app-level icon to keep all windows visually consistent."""
    app = QApplication.instance()
    if app is not None:
        icon = app.windowIcon()
        if not icon.isNull():
            return icon
    return _resolve_feedback_icon()


def _rounded_padded_pixmap(src: QPixmap, size: int) -> QPixmap:
    """Render icon with transparent padding + rounded corners for macOS."""
    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    padding = max(1, int(size * 0.12))
    target = QRect(padding, padding, size - 2 * padding, size - 2 * padding)
    if target.width() <= 0 or target.height() <= 0:
        painter.end()
        return src

    fitted = src.scaled(target.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    draw_rect = QRect(
        target.x() + (target.width() - fitted.width()) // 2,
        target.y() + (target.height() - fitted.height()) // 2,
        fitted.width(),
        fitted.height(),
    )
    radius = max(2.0, float(min(target.width(), target.height())) * 0.22)
    clip_path = QPainterPath()
    clip_path.addRoundedRect(target, radius, radius)
    painter.setClipPath(clip_path)
    painter.drawPixmap(draw_rect, fitted)
    painter.end()
    return canvas


def _optimize_icon_for_platform(icon: QIcon) -> QIcon:
    """On macOS, shape + pad icon so Dock/title visual size is more native-like."""
    if icon.isNull() or sys.platform != "darwin":
        return icon
    sizes = [16, 20, 24, 32, 48, 64, 128, 256, 512, 1024]
    optimized = QIcon()
    for side in sizes:
        src = icon.pixmap(side, side)
        if src.isNull():
            continue
        optimized.addPixmap(_rounded_padded_pixmap(src, side))
    return optimized if not optimized.isNull() else icon


class _DelayedTooltipFilter(QObject):
    """Show widget tooltips with a configurable delay."""

    def __init__(self, delay_ms: int = 2000, parent=None):
        super().__init__(parent)
        self.delay_ms = delay_ms
        self._timers: dict[int, QTimer] = {}

    def eventFilter(self, obj, event):
        evt_type = event.type()
        key = id(obj)
        if evt_type == QEvent.Enter:
            tip = obj.toolTip() if hasattr(obj, "toolTip") else ""
            if tip:
                timer = self._timers.get(key)
                if timer is None:
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(lambda o=obj: QToolTip.showText(QCursor.pos(), o.toolTip(), o))
                    self._timers[key] = timer
                timer.start(self.delay_ms)
        elif evt_type in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.FocusOut):
            timer = self._timers.get(key)
            if timer is not None:
                timer.stop()
                QToolTip.hideText()
        return super().eventFilter(obj, event)


class FeedbackResult(TypedDict, total=False):
    interactive_feedback: str
    images: List[str]
    temp_images: List[str]

def get_dark_mode_palette(app: QApplication):
    darkPalette = app.palette()
    darkPalette.setColor(QPalette.Window, QColor(53, 53, 53))
    darkPalette.setColor(QPalette.WindowText, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.Base, QColor(42, 42, 42))
    darkPalette.setColor(QPalette.AlternateBase, QColor(66, 66, 66))
    darkPalette.setColor(QPalette.ToolTipBase, QColor(53, 53, 53))
    darkPalette.setColor(QPalette.ToolTipText, Qt.white)
    darkPalette.setColor(QPalette.Text, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.Text, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.Dark, QColor(35, 35, 35))
    darkPalette.setColor(QPalette.Shadow, QColor(20, 20, 20))
    darkPalette.setColor(QPalette.Button, QColor(53, 53, 53))
    darkPalette.setColor(QPalette.ButtonText, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.BrightText, Qt.red)
    darkPalette.setColor(QPalette.Link, QColor(42, 130, 218))
    darkPalette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    darkPalette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(80, 80, 80))
    darkPalette.setColor(QPalette.HighlightedText, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.PlaceholderText, QColor(127, 127, 127))
    return darkPalette

class FeedbackTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            mods = event.modifiers()
            if mods & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier):
                # Ctrl/Cmd/Alt + Enter => submit
                parent = self.parent()
                while parent and not isinstance(parent, FeedbackUI):
                    parent = parent.parent()
                if parent:
                    parent._submit_feedback()
                return
        if event.matches(QKeySequence.Paste):
            # Find the parent FeedbackUI instance and call submit
            parent = self.parent()
            while parent and not isinstance(parent, FeedbackUI):
                parent = parent.parent()
            if parent and parent._paste_images_from_clipboard():
                return
        super().keyPressEvent(event)

class _ImageThumb(QWidget):
    """Thumbnail widget with a small remove button."""

    removed = Signal(object)
    edit_requested = Signal(object)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setStyleSheet(
            """
            QWidget {
                background: #2b2f36;
                border: 1px solid #3d424d;
                border-radius: 8px;
            }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._preview_lbl)

        self._name_lbl = QLabel("")
        self._name_lbl.setAlignment(Qt.AlignCenter)
        self._name_lbl.setStyleSheet("font-size: 10px; color: #b8bec8; background: transparent; border: none;")
        lay.addWidget(self._name_lbl)

        self._remove_btn = QPushButton("✕", self)
        self._remove_btn.setFixedSize(18, 18)
        self._remove_btn.setCursor(Qt.PointingHandCursor)
        self._remove_btn.setStyleSheet(
            "QPushButton { background: rgba(20,20,20,0.82); color: #ffffff; border: 1px solid rgba(255,255,255,0.18); border-radius: 9px; font-size: 11px; padding: 0px; }"
            "QPushButton:hover { background: rgba(190,50,50,0.95); border: 1px solid rgba(255,255,255,0.28); }"
        )
        self._remove_btn.clicked.connect(lambda: self.removed.emit(self))
        self._remove_btn.hide()

        self.setFixedWidth(_THUMB_MAX + 16)
        self.setToolTip("Double-click to edit this image")
        self._refresh_view()
        self._position_remove_btn()

    def _refresh_view(self) -> None:
        pix = QPixmap(self.path)
        if not pix.isNull():
            pix = pix.scaled(
                QSize(_THUMB_MAX, _THUMB_MAX),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        self._preview_lbl.setPixmap(pix)

        name = os.path.basename(self.path)
        if len(name) > 18:
            name = name[:15] + "..."
        self._name_lbl.setText(name)

    def set_path(self, path: str) -> None:
        self.path = path
        self._refresh_view()

    def _position_remove_btn(self) -> None:
        x = self.width() - self._remove_btn.width() - 6
        y = 6
        self._remove_btn.move(x, y)

    def resizeEvent(self, event):
        self._position_remove_btn()
        super().resizeEvent(event)

    def enterEvent(self, event):
        self._remove_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._remove_btn.hide()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.edit_requested.emit(self)
            return
        super().mouseDoubleClickEvent(event)


class _ScreenRegionSelector(QDialog):
    """Cross-platform region selector over a desktop screenshot."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setModal(True)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)

        self._desktop_geometry, self._desktop_shot = self._capture_desktop()
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._selected_rect: QRect | None = None

        self.setGeometry(self._desktop_geometry)

    def _capture_desktop(self) -> tuple[QRect, QPixmap]:
        screens = QGuiApplication.screens()
        if not screens:
            fallback = QRect(0, 0, 1280, 720)
            empty = QPixmap(fallback.size())
            empty.fill(Qt.black)
            return fallback, empty

        desktop_rect = screens[0].geometry()
        for screen in screens[1:]:
            desktop_rect = desktop_rect.united(screen.geometry())

        shot = QPixmap(desktop_rect.size())
        shot.fill(Qt.black)
        painter = QPainter(shot)
        origin = desktop_rect.topLeft()
        for screen in screens:
            geo = screen.geometry()
            snap = screen.grabWindow(0)
            painter.drawPixmap(geo.topLeft() - origin, snap)
        painter.end()
        return desktop_rect, shot

    def _current_rect(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        rect = QRect(self._start, self._end).normalized()
        if rect.width() < 4 or rect.height() < 4:
            return None
        return rect

    def selected_pixmap(self) -> QPixmap | None:
        if self._selected_rect is None:
            return None
        global_rect = self._selected_rect.translated(self._desktop_geometry.topLeft())
        center = global_rect.center()
        screen = QGuiApplication.screenAt(center)
        if screen is not None:
            geo = screen.geometry()
            local_x = global_rect.x() - geo.x()
            local_y = global_rect.y() - geo.y()
            grab = screen.grabWindow(0, local_x, local_y, global_rect.width(), global_rect.height())
            if not grab.isNull():
                return grab
        return self._desktop_shot.copy(self._selected_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start = event.position().toPoint()
            self._end = self._start
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None:
            self._end = event.position().toPoint()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._start is not None:
            self._end = event.position().toPoint()
            self._selected_rect = self._current_rect()
            if self._selected_rect is not None:
                self.accept()
            else:
                self._start = None
                self._end = None
                self.update()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._desktop_shot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        rect = self._current_rect()
        if rect is not None:
            painter.drawPixmap(rect, self._desktop_shot, rect)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(42, 130, 218), 2))
            painter.drawRect(rect)

        tip = "Drag to capture region • Esc to cancel"
        painter.setPen(QColor(240, 240, 240))
        painter.setBrush(QColor(0, 0, 0, 140))
        tip_rect = QRect(20, 20, 320, 30)
        painter.drawRoundedRect(tip_rect, 6, 6)
        painter.drawText(tip_rect.adjusted(10, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, tip)
        painter.end()
        super().paintEvent(event)


class _ImageAnnotatorCanvas(QWidget):
    """Lightweight image annotation canvas with multiple tools."""

    text_requested = Signal(QPoint)
    resized = Signal()

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self._base_pixmap = pixmap.copy()
        # Normalize high-DPI pixmaps so widget size and draw size stay consistent.
        # Without this, Retina captures may render in the top-left corner.
        self._base_pixmap.setDevicePixelRatio(1.0)
        self._ops: list[dict] = []
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._draft_points: list[QPoint] = []
        self._tool = "rect"
        self._pen_color = QColor(255, 70, 70)
        self._pen_width = 3
        self.setFixedSize(self._base_pixmap.size())
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.CrossCursor)

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self._start = None
        self._end = None
        self._draft_points = []
        self.update()

    def undo_last(self) -> None:
        if self._ops:
            self._ops.pop()
            self.update()

    def clear_all(self) -> None:
        self._ops.clear()
        self._start = None
        self._end = None
        self._draft_points = []
        self.update()

    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        self.update()

    def current_pen_color(self) -> QColor:
        return QColor(self._pen_color)

    def set_pen_width(self, width: int) -> None:
        self._pen_width = max(1, min(12, int(width)))
        self.update()

    def original_pixmap(self) -> QPixmap:
        return self._base_pixmap.copy()

    def _active_rect(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        rect = QRect(self._start, self._end).normalized()
        if rect.width() < 6 or rect.height() < 6:
            return None
        return rect

    def render_result(self) -> QPixmap:
        out = self._base_pixmap.copy()
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for op in self._ops:
            self._draw_op(painter, op)
        painter.end()
        return out

    def add_text(self, pos: QPoint, text: str) -> None:
        if not text.strip():
            return
        self._ops.append(
            {
                "type": "text",
                "pos": QPoint(pos),
                "text": text.strip(),
                "color": QColor(self._pen_color),
                "width": self._pen_width,
            }
        )
        self.update()

    def _draw_arrow(self, painter: QPainter, p1: QPoint, p2: QPoint, color: QColor, width: int) -> None:
        pen = QPen(color, width)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        head_len = 12 + width
        left = QPoint(
            int(p2.x() - head_len * math.cos(angle - math.pi / 6)),
            int(p2.y() - head_len * math.sin(angle - math.pi / 6)),
        )
        right = QPoint(
            int(p2.x() - head_len * math.cos(angle + math.pi / 6)),
            int(p2.y() - head_len * math.sin(angle + math.pi / 6)),
        )
        painter.drawLine(p2, left)
        painter.drawLine(p2, right)

    def _draw_op(self, painter: QPainter, op: dict) -> None:
        color = op.get("color", QColor(255, 70, 70))
        width = op.get("width", 3)
        painter.setPen(QPen(color, width))
        t = op.get("type")

        if t == "rect":
            painter.drawRect(op["rect"])
        elif t == "circle":
            painter.drawEllipse(op["rect"])
        elif t == "arrow":
            self._draw_arrow(painter, op["p1"], op["p2"], color, width)
        elif t == "pen":
            points = op.get("points", [])
            if len(points) == 1:
                painter.drawPoint(points[0])
            elif len(points) > 1:
                for i in range(1, len(points)):
                    painter.drawLine(points[i - 1], points[i])
        elif t == "text":
            painter.drawText(op["pos"], op["text"])

    def _apply_crop(self, rect: QRect) -> None:
        if rect.width() < 8 or rect.height() < 8:
            return
        self._base_pixmap = self._base_pixmap.copy(rect)
        self._ops.clear()
        self._start = None
        self._end = None
        self._draft_points = []
        self.setFixedSize(self._base_pixmap.size())
        self.updateGeometry()
        self.update()
        self.resized.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._tool == "text":
                self.text_requested.emit(event.position().toPoint())
                return
            self._start = event.position().toPoint()
            self._end = self._start
            if self._tool == "pen":
                self._draft_points = [self._start]
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None:
            point = event.position().toPoint()
            self._end = point
            if self._tool == "pen":
                self._draft_points.append(point)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._start is not None:
            self._end = event.position().toPoint()
            if self._tool == "pen":
                points = [QPoint(p) for p in self._draft_points]
                if points:
                    self._ops.append(
                        {
                            "type": "pen",
                            "points": points,
                            "color": QColor(self._pen_color),
                            "width": self._pen_width,
                        }
                    )
            else:
                rect = self._active_rect()
                if rect is not None:
                    if self._tool == "rect":
                        self._ops.append(
                            {
                                "type": "rect",
                                "rect": rect,
                                "color": QColor(self._pen_color),
                                "width": self._pen_width,
                            }
                        )
                    elif self._tool == "circle":
                        self._ops.append(
                            {
                                "type": "circle",
                                "rect": rect,
                                "color": QColor(self._pen_color),
                                "width": self._pen_width,
                            }
                        )
                    elif self._tool == "arrow":
                        self._ops.append(
                            {
                                "type": "arrow",
                                "p1": QPoint(self._start),
                                "p2": QPoint(self._end),
                                "color": QColor(self._pen_color),
                                "width": self._pen_width,
                            }
                        )
                    elif self._tool == "crop":
                        self._apply_crop(rect)
            self._start = None
            self._end = None
            self._draft_points = []
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._base_pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for op in self._ops:
            self._draw_op(painter, op)

        if self._tool == "pen" and self._draft_points:
            self._draw_op(
                painter,
                {
                    "type": "pen",
                    "points": self._draft_points,
                    "color": QColor(self._pen_color),
                    "width": self._pen_width,
                },
            )
        else:
            active = self._active_rect()
            if active is not None:
                if self._tool == "rect":
                    self._draw_op(
                        painter,
                        {"type": "rect", "rect": active, "color": QColor(self._pen_color), "width": self._pen_width},
                    )
                elif self._tool == "circle":
                    self._draw_op(
                        painter,
                        {"type": "circle", "rect": active, "color": QColor(self._pen_color), "width": self._pen_width},
                    )
                elif self._tool == "arrow":
                    self._draw_op(
                        painter,
                        {
                            "type": "arrow",
                            "p1": QPoint(self._start),
                            "p2": QPoint(self._end),
                            "color": QColor(self._pen_color),
                            "width": self._pen_width,
                        },
                    )
                elif self._tool == "crop":
                    self._draw_op(
                        painter,
                        {"type": "rect", "rect": active, "color": QColor(255, 220, 60), "width": max(2, self._pen_width)},
                    )
        painter.end()
        super().paintEvent(event)


class _ImageAnnotatorDialog(QDialog):
    """Annotation dialog with WeChat-like bottom tool row."""

    def __init__(self, pixmap: QPixmap):
        super().__init__(None)
        self.setModal(True)
        self.setWindowTitle("Annotate Screenshot")
        self.setWindowIcon(_effective_feedback_icon())
        self.annotated_pixmap: QPixmap | None = None
        self.setStyleSheet(
            """
            QDialog { background: #2b2f35; color: #ecf0f4; }
            QLabel { color: #cfd6df; }
            QScrollArea { background: #23272d; border: 1px solid #3a404a; border-radius: 8px; }
            QPushButton {
                background: #3a404a;
                color: #ecf0f4;
                border: 1px solid #4f5765;
                border-radius: 6px;
                padding: 5px 10px;
            }
            QPushButton:hover { background: #474f5c; }
            QPushButton:checked { background: #3f78b8; border: 1px solid #5490d4; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        hint = QLabel(
            "Help: Cmd/Alt/Ctrl+Enter confirm · Cmd/Ctrl+Z undo · X crop mode. "
            "Hover controls for 2s to see tips."
        )
        hint.setStyleSheet("color: #aeb6c0;")
        layout.addWidget(hint)

        self.canvas = _ImageAnnotatorCanvas(pixmap)
        scroll = QScrollArea()
        scroll.setAlignment(Qt.AlignCenter)
        scroll.setWidgetResizable(True)
        canvas_host = QWidget()
        host_layout = QGridLayout(canvas_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setRowStretch(0, 1)
        host_layout.setRowStretch(2, 1)
        host_layout.setColumnStretch(0, 1)
        host_layout.setColumnStretch(2, 1)
        host_layout.addWidget(self.canvas, 1, 1, alignment=Qt.AlignCenter)
        scroll.setWidget(canvas_host)
        layout.addWidget(scroll, stretch=1)

        self._tooltip_filter = _DelayedTooltipFilter(2000, self)
        tool_group = QButtonGroup(self)
        tool_group.setExclusive(True)

        def _install_delayed_tooltip(widget: QWidget, tip: str) -> None:
            widget.setToolTip(tip)
            widget.installEventFilter(self._tooltip_filter)

        def _make_tool_btn(label: str, tool_name: str, tip: str, checked: bool = False) -> QPushButton:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(checked)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(38)
            tool_group.addButton(btn)
            btn.clicked.connect(lambda: self.canvas.set_tool(tool_name))
            _install_delayed_tooltip(btn, tip)
            return btn

        row1 = QHBoxLayout()
        tools_lbl = QLabel("Tools")
        tools_lbl.setStyleSheet("font-weight: 600; color: #d7deea;")
        row1.addWidget(tools_lbl)
        rect_btn = _make_tool_btn("▭", "rect", "Rectangle tool (R)", checked=True)
        circle_btn = _make_tool_btn("◯", "circle", "Circle tool (C)")
        arrow_btn = _make_tool_btn("↗", "arrow", "Arrow tool (A)")
        pen_btn = _make_tool_btn("✎", "pen", "Pen tool (P)")
        text_btn = _make_tool_btn("T", "text", "Text tool (T)")
        crop_btn = _make_tool_btn("✂", "crop", "Crop tool (X)")
        row1.addWidget(rect_btn)
        row1.addWidget(circle_btn)
        row1.addWidget(arrow_btn)
        row1.addWidget(pen_btn)
        row1.addWidget(text_btn)
        row1.addWidget(crop_btn)
        sep1 = QLabel(" | ")
        sep1.setStyleSheet("color: #7f8792;")
        row1.addWidget(sep1)
        colors_lbl = QLabel("Colors")
        colors_lbl.setStyleSheet("font-weight: 600; color: #d7deea;")
        row1.addWidget(colors_lbl)

        red_btn = QPushButton("R")
        red_btn.setFixedHeight(26)
        red_btn.clicked.connect(lambda: self.canvas.set_pen_color(QColor(255, 70, 70)))
        _install_delayed_tooltip(red_btn, "Red color")
        yellow_btn = QPushButton("Y")
        yellow_btn.setFixedHeight(26)
        yellow_btn.clicked.connect(lambda: self.canvas.set_pen_color(QColor(255, 220, 50)))
        _install_delayed_tooltip(yellow_btn, "Yellow color")
        green_btn = QPushButton("G")
        green_btn.setFixedHeight(26)
        green_btn.clicked.connect(lambda: self.canvas.set_pen_color(QColor(60, 210, 120)))
        _install_delayed_tooltip(green_btn, "Green color")
        palette_btn = QPushButton("🎨")
        palette_btn.setFixedHeight(26)
        palette_btn.clicked.connect(self._pick_custom_color)
        _install_delayed_tooltip(palette_btn, "Custom color palette")
        row1.addWidget(red_btn)
        row1.addWidget(yellow_btn)
        row1.addWidget(green_btn)
        row1.addWidget(palette_btn)
        row1.addStretch()
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        stroke_lbl = QLabel("Stroke")
        stroke_lbl.setStyleSheet("font-weight: 600; color: #d7deea;")
        row2.addWidget(stroke_lbl)
        thin_btn = QPushButton("2px")
        thin_btn.setFixedHeight(26)
        thin_btn.clicked.connect(lambda: self.canvas.set_pen_width(2))
        _install_delayed_tooltip(thin_btn, "Thin stroke")
        normal_btn = QPushButton("4px")
        normal_btn.setFixedHeight(26)
        normal_btn.clicked.connect(lambda: self.canvas.set_pen_width(4))
        _install_delayed_tooltip(normal_btn, "Normal stroke")
        thick_btn = QPushButton("6px")
        thick_btn.setFixedHeight(26)
        thick_btn.clicked.connect(lambda: self.canvas.set_pen_width(6))
        _install_delayed_tooltip(thick_btn, "Thick stroke")
        undo_btn = QPushButton("Undo")
        undo_btn.setFixedHeight(26)
        undo_btn.clicked.connect(self.canvas.undo_last)
        _install_delayed_tooltip(undo_btn, "Undo last action (Cmd/Ctrl+Z)")
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(26)
        clear_btn.clicked.connect(self.canvas.clear_all)
        _install_delayed_tooltip(clear_btn, "Clear all annotations")
        sep2 = QLabel(" | ")
        sep2.setStyleSheet("color: #7f8792;")
        actions_lbl = QLabel("Result")
        actions_lbl.setStyleSheet("font-weight: 600; color: #d7deea;")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(26)
        cancel_btn.clicked.connect(self.reject)
        _install_delayed_tooltip(cancel_btn, "Cancel and close (Esc)")
        use_original_btn = QPushButton("Original")
        use_original_btn.setFixedHeight(26)
        use_original_btn.clicked.connect(self._accept_original)
        _install_delayed_tooltip(use_original_btn, "Use unmodified image")
        save_btn = QPushButton("Confirm")
        save_btn.setFixedHeight(26)
        save_btn.clicked.connect(self._accept_result)
        _install_delayed_tooltip(save_btn, "Confirm image (Cmd/Alt/Ctrl+Enter)")

        row2.addWidget(thin_btn)
        row2.addWidget(normal_btn)
        row2.addWidget(thick_btn)
        row2.addWidget(undo_btn)
        row2.addWidget(clear_btn)
        row2.addWidget(sep2)
        row2.addWidget(actions_lbl)
        row2.addStretch()
        row2.addWidget(cancel_btn)
        row2.addWidget(use_original_btn)
        row2.addWidget(save_btn)
        layout.addLayout(row2)

        # Shortcuts
        QShortcut(QKeySequence("Meta+Return"), self).activated.connect(self._accept_result)
        QShortcut(QKeySequence("Alt+Return"), self).activated.connect(self._accept_result)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._accept_result)
        QShortcut(QKeySequence("Meta+Z"), self).activated.connect(self.canvas.undo_last)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.canvas.undo_last)
        QShortcut(QKeySequence("Meta+Shift+X"), self).activated.connect(self.canvas.clear_all)
        QShortcut(QKeySequence("Ctrl+Shift+X"), self).activated.connect(self.canvas.clear_all)
        QShortcut(QKeySequence("R"), self).activated.connect(rect_btn.click)
        QShortcut(QKeySequence("C"), self).activated.connect(circle_btn.click)
        QShortcut(QKeySequence("A"), self).activated.connect(arrow_btn.click)
        QShortcut(QKeySequence("P"), self).activated.connect(pen_btn.click)
        QShortcut(QKeySequence("T"), self).activated.connect(text_btn.click)
        QShortcut(QKeySequence("X"), self).activated.connect(crop_btn.click)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.reject)

        self.canvas.text_requested.connect(self._request_text)
        self.canvas.resized.connect(lambda: self._resize_to_canvas(center=False))
        self._resize_to_canvas(center=True)

    def _accept_result(self):
        self.annotated_pixmap = self.canvas.render_result()
        self.accept()

    def _accept_original(self):
        self.annotated_pixmap = self.canvas.original_pixmap()
        self.accept()

    def _request_text(self, pos: QPoint):
        text, ok = QInputDialog.getText(self, "Add Text", "Text:")
        if ok and text.strip():
            self.canvas.add_text(pos, text.strip())

    def _pick_custom_color(self):
        color = QColorDialog.getColor(self.canvas.current_pen_color(), self, "Pick annotation color")
        if color.isValid():
            self.canvas.set_pen_color(color)

    def _resize_to_canvas(self, center: bool = False):
        screen_geo = QGuiApplication.primaryScreen().availableGeometry()
        max_w = max(420, screen_geo.width() - 24)
        max_h = max(320, screen_geo.height() - 24)
        min_w = min(max_w, 820)
        min_h = min(max_h, 500)
        preferred_w = min(max_w, max(min_w, self.canvas.width() + 28))
        preferred_h = min(max_h, max(min_h, self.canvas.height() + 168))

        self.setMinimumSize(min_w, min_h)
        self.setMaximumSize(max_w, max_h)
        self.resize(preferred_w, preferred_h)
        if center:
            self.move(screen_geo.center() - self.rect().center())


class FeedbackUI(QMainWindow):
    remote_image_loaded = Signal(str, object)
    remote_image_failed = Signal(str, str)

    def __init__(
        self,
        prompt: str,
        predefined_options: Optional[List[str]] = None,
        prompt_images: Optional[List[str]] = None,
    ):
        super().__init__()
        self.prompt = prompt
        self.predefined_options = predefined_options or []
        self.prompt_images = self._normalize_prompt_images(prompt, prompt_images or [])
        self._prompt_image_labels: dict[str, QLabel] = {}
        self._image_paths: list[str] = []
        self._temp_image_paths: set[str] = set()
        self._capture_hidden_window = False

        self.feedback_result = None
        
        self.setWindowTitle("Interactive Feedback MCP")
        self.setWindowIcon(_effective_feedback_icon())
        
        self.settings = QSettings("InteractiveFeedbackMCP", "InteractiveFeedbackMCP")
        
        # Load general UI settings for the main window (geometry, state)
        self.settings.beginGroup("MainWindow_General")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(800, 600)
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - 800) // 2
            y = (screen.height() - 600) // 2
            self.move(x, y)
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)
        self.settings.endGroup() # End "MainWindow_General" group

        self._tooltip_filter = _DelayedTooltipFilter(2000, self)
        self.remote_image_loaded.connect(self._on_remote_image_loaded)
        self.remote_image_failed.connect(self._on_remote_image_failed)
        self._create_ui()
        self._apply_styles()

    @staticmethod
    def _extract_image_sources_from_prompt(prompt: str) -> List[str]:
        if not isinstance(prompt, str) or not prompt.strip():
            return []
        sources: List[str] = []
        markdown_sources = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", prompt)
        sources.extend(markdown_sources)
        url_sources = re.findall(r"(https?://[^\s)>\"]+|file://[^\s)>\"]+)", prompt)
        sources.extend(url_sources)
        return sources

    @staticmethod
    def _load_prompt_image_source(source: str) -> Optional[QPixmap]:
        if not source:
            return None
        candidate = source.strip().strip("<>").strip("'").strip('"')
        if not candidate:
            return None

        parsed = urlparse(candidate)
        scheme = (parsed.scheme or "").lower()

        if scheme == "file":
            local_path = unquote(parsed.path or "")
            if local_path and os.path.isfile(local_path):
                pix = QPixmap(local_path)
                if not pix.isNull():
                    return pix
            return None

        local_candidate = os.path.expanduser(candidate)
        if os.path.isfile(local_candidate):
            pix = QPixmap(local_candidate)
            if not pix.isNull():
                return pix
        return None

    @staticmethod
    def _is_remote_prompt_image_source(source: str) -> bool:
        candidate = source.strip().strip("<>").strip("'").strip('"')
        if not candidate:
            return False
        scheme = (urlparse(candidate).scheme or "").lower()
        return scheme in ("http", "https")

    @classmethod
    def _normalize_prompt_images(cls, prompt: str, explicit_sources: List[str]) -> List[str]:
        sources: List[str] = []
        for src in explicit_sources:
            if isinstance(src, str) and src.strip():
                sources.append(src.strip())
        sources.extend(cls._extract_image_sources_from_prompt(prompt))

        normalized: List[str] = []
        seen: set[str] = set()
        for source in sources:
            key = source.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized

    def _fetch_remote_prompt_image(self, source: str) -> None:
        def _worker():
            try:
                req = urllib.request.Request(source, headers={"User-Agent": "interactive-feedback-mcp/1.0"})
                with urllib.request.urlopen(req, timeout=_REMOTE_IMAGE_TIMEOUT_SEC) as resp:
                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if ctype and not ctype.startswith("image/"):
                        self.remote_image_failed.emit(source, "non-image content type")
                        return
                    data = resp.read(_REMOTE_IMAGE_MAX_BYTES + 1)
                    if len(data) > _REMOTE_IMAGE_MAX_BYTES:
                        self.remote_image_failed.emit(source, "image too large")
                        return
                pix = QPixmap()
                if pix.loadFromData(data):
                    self.remote_image_loaded.emit(source, pix)
                    return
                self.remote_image_failed.emit(source, "decode failed")
            except Exception as e:
                self.remote_image_failed.emit(source, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_remote_image_loaded(self, source: str, pix: QPixmap) -> None:
        preview = self._prompt_image_labels.get(source)
        if preview is None:
            return
        preview.setText("")
        preview.setPixmap(
            pix.scaled(
                QSize(220, 150),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def _on_remote_image_failed(self, source: str, reason: str) -> None:
        preview = self._prompt_image_labels.get(source)
        if preview is None:
            return
        preview.setPixmap(QPixmap())
        preview.setText("Image load failed")
        preview.setToolTip(f"{source}\n{reason}")

    def _bind_delayed_tooltip(self, widget: QWidget, text: str) -> None:
        widget.setToolTip(text)
        widget.installEventFilter(self._tooltip_filter)

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QMainWindow { background: #2c2f34; }
            QLabel { color: #eceff4; }
            QLabel#hintLabel { color: #a8b0bb; }
            QFrame#promptCard {
                background: #30343b;
                border: 1px solid #424853;
                border-radius: 10px;
            }
            QTextEdit {
                background: #30343b;
                border: 1px solid #454c58;
                border-radius: 10px;
                color: #f3f5f8;
                padding: 8px;
            }
            QTextEdit:focus {
                border: 1px solid #6f96c6;
            }
            QPushButton {
                background: #3a404a;
                color: #f0f2f6;
                border: 1px solid #4e5663;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #464d59; }
            QPushButton#primaryButton {
                background: #3973b3;
                border: 1px solid #4a87cb;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover { background: #4a82c2; }
            QCheckBox { color: #d2d8e1; }
            QScrollArea#thumbScroll {
                background: #25292f;
                border: 1px solid #3b414b;
                border-radius: 10px;
            }
            QLabel#promptImage {
                background: #25292f;
                border: 1px solid #3b414b;
                border-radius: 8px;
                padding: 2px;
            }
            """
        )

    def _create_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ── Scrollable content area for prompt and options ──
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(8)

        help_label = QLabel(
            "Help: paste image with Ctrl/Cmd+V in input box · send with Cmd/Alt/Ctrl+Enter · "
            "double-click thumbnail to re-edit. Prompt supports http(s)/file image URLs."
        )
        help_label.setObjectName("hintLabel")
        help_label.setWordWrap(True)
        content_layout.addWidget(help_label)

        # Description label (from self.prompt) - Support multiline
        prompt_card = QFrame()
        prompt_card.setObjectName("promptCard")
        prompt_layout = QVBoxLayout(prompt_card)
        prompt_layout.setContentsMargins(10, 10, 10, 10)
        prompt_layout.setSpacing(6)

        self.description_label = QLabel(self.prompt)
        self.description_label.setWordWrap(True)
        self.description_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.description_label.setCursor(Qt.IBeamCursor)
        prompt_layout.addWidget(self.description_label)
        content_layout.addWidget(prompt_card)

        if self.prompt_images:
            prompt_images_label = QLabel("Images from assistant:")
            prompt_images_label.setObjectName("hintLabel")
            content_layout.addWidget(prompt_images_label)

            prompt_images_scroll = QScrollArea()
            prompt_images_scroll.setObjectName("thumbScroll")
            prompt_images_scroll.setWidgetResizable(True)
            prompt_images_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            prompt_images_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            prompt_images_scroll.setFixedHeight(170)

            prompt_images_host = QWidget()
            prompt_images_layout = QHBoxLayout(prompt_images_host)
            prompt_images_layout.setContentsMargins(4, 4, 4, 4)
            prompt_images_layout.setSpacing(8)
            for img_source in self.prompt_images:
                preview = QLabel()
                preview.setObjectName("promptImage")
                preview.setAlignment(Qt.AlignCenter)
                preview.setToolTip(img_source)
                preview.setMinimumSize(220, 150)
                pix = self._load_prompt_image_source(img_source)
                if pix is not None and not pix.isNull():
                    preview.setPixmap(
                        pix.scaled(
                            QSize(220, 150),
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                    )
                elif self._is_remote_prompt_image_source(img_source):
                    preview.setText("Loading URL image...")
                    self._prompt_image_labels[img_source] = preview
                    self._fetch_remote_prompt_image(img_source)
                else:
                    preview.setText("Image not found")
                prompt_images_layout.addWidget(preview)
            prompt_images_layout.addStretch()
            prompt_images_scroll.setWidget(prompt_images_host)
            content_layout.addWidget(prompt_images_scroll)

        # Add predefined options if any
        self.option_checkboxes = []
        if self.predefined_options and len(self.predefined_options) > 0:
            options_frame = QFrame()
            options_layout = QVBoxLayout(options_frame)
            options_layout.setContentsMargins(0, 10, 0, 10)

            for option in self.predefined_options:
                checkbox = QCheckBox(option)
                self.option_checkboxes.append(checkbox)
                options_layout.addWidget(checkbox)

            content_layout.addWidget(options_frame)

            # Add a separator
            separator = QFrame()
            separator.setFrameShape(QFrame.HLine)
            separator.setFrameShadow(QFrame.Sunken)
            content_layout.addWidget(separator)

        content_layout.addStretch()
        content_scroll.setWidget(content_widget)
        main_layout.addWidget(content_scroll, stretch=1)

        # ── Fixed bottom area: text input, images, submit ──
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        # Free-form text feedback
        self.feedback_text = FeedbackTextEdit()
        font_metrics = self.feedback_text.fontMetrics()
        row_height = font_metrics.height()
        padding = self.feedback_text.contentsMargins().top() + self.feedback_text.contentsMargins().bottom() + 5
        self.feedback_text.setMinimumHeight(5 * row_height + padding)
        self.feedback_text.setMaximumHeight(10 * row_height + padding)  # Limit max height

        self.feedback_text.setPlaceholderText("Enter your feedback here (Cmd/Alt/Ctrl+Enter to submit)")
        self._bind_delayed_tooltip(
            self.feedback_text,
            "You can paste image directly here with Ctrl/Cmd+V.",
        )
        bottom_layout.addWidget(self.feedback_text)
        paste_hint = QLabel("Tip: paste image directly in the input box with Ctrl+V")
        paste_hint.setObjectName("hintLabel")
        bottom_layout.addWidget(paste_hint)

        # ── Image attachment area ──
        img_bar = QHBoxLayout()
        add_img_btn = QPushButton("📎 Add Image…")
        add_img_btn.setObjectName("secondaryButton")
        self._bind_delayed_tooltip(add_img_btn, "Attach local image files")
        add_img_btn.clicked.connect(self._pick_images)
        img_bar.addWidget(add_img_btn)

        shot_btn = QPushButton("📸 Capture Screenshot…")
        shot_btn.setObjectName("secondaryButton")
        self._bind_delayed_tooltip(shot_btn, "Capture and annotate screenshot")
        shot_btn.clicked.connect(self._capture_screenshot)
        img_bar.addWidget(shot_btn)

        self._hide_window_check = QCheckBox("Hide this window while capturing")
        self._hide_window_check.setChecked(True)
        self._bind_delayed_tooltip(self._hide_window_check, "Hide feedback window during capture")
        img_bar.addWidget(self._hide_window_check)

        self._img_count_label = QLabel("")
        self._img_count_label.setObjectName("hintLabel")
        self._img_count_label.setStyleSheet("color: #888;")
        img_bar.addWidget(self._img_count_label)
        img_bar.addStretch()
        bottom_layout.addLayout(img_bar)

        # Scrollable thumbnail strip (hidden until images are added)
        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setObjectName("thumbScroll")
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setFixedHeight(_THUMB_MAX + 70)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._thumb_scroll.setVisible(False)

        self._thumb_container = QWidget()
        self._thumb_layout = QHBoxLayout(self._thumb_container)
        self._thumb_layout.setContentsMargins(0, 0, 0, 0)
        self._thumb_layout.setSpacing(4)
        self._thumb_layout.addStretch()
        self._thumb_scroll.setWidget(self._thumb_container)
        bottom_layout.addWidget(self._thumb_scroll)

        submit_button = QPushButton("&Send Feedback")
        submit_button.setObjectName("primaryButton")
        self._bind_delayed_tooltip(submit_button, "Send feedback now (Cmd/Alt/Ctrl+Enter)")
        submit_button.clicked.connect(self._submit_feedback)
        bottom_layout.addWidget(submit_button)
        QShortcut(QKeySequence("Meta+Return"), self).activated.connect(self._submit_feedback)
        QShortcut(QKeySequence("Alt+Return"), self).activated.connect(self._submit_feedback)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._submit_feedback)

        main_layout.addWidget(bottom_widget)

    # ── Image helpers ──

    @staticmethod
    def _is_image_file_path(path: str) -> bool:
        ext = os.path.splitext(path.lower())[1]
        return ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

    @staticmethod
    def _is_loadable_image(path: str) -> bool:
        if not os.path.isfile(path):
            return False
        pix = QPixmap(path)
        return not pix.isNull()

    def _cleanup_temp_images(self):
        for path in list(self._temp_image_paths):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass
            self._temp_image_paths.discard(path)

    def _capture_screenshot(self):
        self._capture_hidden_window = bool(self._hide_window_check.isChecked())
        if self._capture_hidden_window:
            try:
                self.hide()
                QApplication.processEvents()
                QTimer.singleShot(150, self._run_capture_flow)
                return
            except Exception:
                # Ensure the window is restored if scheduling capture fails.
                self.show()
                self.raise_()
                self.activateWindow()
                self._capture_hidden_window = False
                return
        self._run_capture_flow()

    def _run_capture_flow(self):
        try:
            selector = _ScreenRegionSelector()
            if selector.exec() != QDialog.Accepted:
                return
            screenshot = selector.selected_pixmap()
            if screenshot is None or screenshot.isNull():
                return

            annotator = _ImageAnnotatorDialog(screenshot)
            if annotator.exec() != QDialog.Accepted or annotator.annotated_pixmap is None:
                return

            path = self._save_temp_screenshot(annotator.annotated_pixmap)
            if path and path not in self._image_paths:
                self._add_image(path)
        finally:
            if self._capture_hidden_window:
                self.show()
                self.raise_()
                self.activateWindow()
            self._capture_hidden_window = False

    def _save_temp_screenshot(self, pixmap: QPixmap) -> Optional[str]:
        fd, path = tempfile.mkstemp(prefix=_SHOT_PREFIX, suffix=".png")
        os.close(fd)
        if not pixmap.save(path, "PNG"):
            try:
                os.remove(path)
            except Exception:
                pass
            return None
        self._temp_image_paths.add(path)
        return path

    def _save_temp_clipboard_bytes(self, data: bytes, suffix: str) -> Optional[str]:
        if not data:
            return None
        fd, path = tempfile.mkstemp(prefix=_CLIP_PREFIX, suffix=suffix)
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(data)
            if not self._is_loadable_image(path):
                os.remove(path)
                return None
            self._temp_image_paths.add(path)
            return path
        except Exception:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass
            return None

    def _paste_images_from_clipboard(self) -> bool:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False

        mime = clipboard.mimeData()
        if mime is None:
            return False

        added = False
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = url.toLocalFile()
                if not path or not os.path.isfile(path):
                    continue
                if not self._is_image_file_path(path):
                    continue
                if not self._is_loadable_image(path):
                    continue
                if path not in self._image_paths:
                    self._add_image(path)
                    added = True

        # If clipboard already contains concrete local image files, prefer them.
        # This avoids adding macOS file-icon previews as extra attachments.
        if added:
            return True

        # Prefer raw clipboard image bytes when available to preserve fidelity.
        raw_formats = (
            ("image/png", ".png"),
            ("image/jpeg", ".jpg"),
            ("image/webp", ".webp"),
            ("image/bmp", ".bmp"),
        )
        for fmt, suffix in raw_formats:
            if not mime.hasFormat(fmt):
                continue
            blob = bytes(mime.data(fmt))
            path = self._save_temp_clipboard_bytes(blob, suffix)
            if path and path not in self._image_paths:
                self._add_image(path)
                return True

        if mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                if not pixmap.isNull():
                    path = self._save_temp_screenshot(pixmap)
                    if path and path not in self._image_paths:
                        self._add_image(path)
                        added = True
        return added

    def _pick_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Images", "", _IMAGE_FILTERS
        )
        for p in paths:
            if p and p not in self._image_paths:
                self._add_image(p)

    def _add_image(self, path: str):
        self._image_paths.append(path)
        thumb = _ImageThumb(path)
        thumb.removed.connect(self._remove_image)
        thumb.edit_requested.connect(self._edit_image_thumb)
        self._thumb_layout.insertWidget(self._thumb_layout.count() - 1, thumb)
        self._refresh_image_ui()

    def _edit_image_thumb(self, thumb: _ImageThumb):
        if not os.path.isfile(thumb.path):
            return
        pix = QPixmap(thumb.path)
        if pix.isNull():
            return

        annotator = _ImageAnnotatorDialog(pix)
        if annotator.exec() != QDialog.Accepted or annotator.annotated_pixmap is None:
            return

        new_path = self._save_temp_screenshot(annotator.annotated_pixmap)
        if not new_path:
            return

        old_path = thumb.path
        try:
            idx = self._image_paths.index(old_path)
            self._image_paths[idx] = new_path
        except ValueError:
            self._image_paths.append(new_path)
        thumb.set_path(new_path)

        if old_path in self._temp_image_paths:
            try:
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except Exception:
                pass
            self._temp_image_paths.discard(old_path)

    def _remove_image(self, thumb: _ImageThumb):
        if thumb.path in self._image_paths:
            self._image_paths.remove(thumb.path)
        if thumb.path in self._temp_image_paths:
            try:
                if os.path.isfile(thumb.path):
                    os.remove(thumb.path)
            except Exception:
                pass
            self._temp_image_paths.discard(thumb.path)
        thumb.setParent(None)
        thumb.deleteLater()
        self._refresh_image_ui()

    def _refresh_image_ui(self):
        n = len(self._image_paths)
        self._thumb_scroll.setVisible(n > 0)
        self._img_count_label.setText(f"{n} image(s) attached" if n else "")

    # ── Submit ──

    def _submit_feedback(self):
        feedback_text = self.feedback_text.toPlainText().strip()
        selected_options = []
        
        if self.option_checkboxes:
            for i, checkbox in enumerate(self.option_checkboxes):
                if checkbox.isChecked():
                    selected_options.append(self.predefined_options[i])
        
        final_feedback_parts = []
        if selected_options:
            final_feedback_parts.append("; ".join(selected_options))
        if feedback_text:
            final_feedback_parts.append(feedback_text)
        final_feedback = "\n\n".join(final_feedback_parts)
            
        self.feedback_result = FeedbackResult(
            interactive_feedback=final_feedback,
            images=list(self._image_paths),
            temp_images=[p for p in self._image_paths if p in self._temp_image_paths],
        )
        self.close()

    def closeEvent(self, event):
        if self.feedback_result is None:
            # User closed window without submitting; remove temporary captures.
            self._cleanup_temp_images()

        # Save general UI settings for the main window (geometry, state)
        self.settings.beginGroup("MainWindow_General")
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.endGroup()

        super().closeEvent(event)

    def run(self) -> FeedbackResult:
        self.show()
        QApplication.instance().exec()

        if not self.feedback_result:
            return FeedbackResult(interactive_feedback="", images=[])

        return self.feedback_result

def feedback_ui(
    prompt: str,
    predefined_options: Optional[List[str]] = None,
    output_file: Optional[str] = None,
    prompt_images: Optional[List[str]] = None,
) -> Optional[FeedbackResult]:
    app = QApplication.instance() or QApplication()
    app.setPalette(get_dark_mode_palette(app))
    app.setStyle("Fusion")
    _apply_app_identity(app, _resolve_feedback_icon())
    ui = FeedbackUI(prompt, predefined_options, prompt_images=prompt_images)
    result = ui.run()

    if output_file and result:
        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
        # Save the result to the output file
        with open(output_file, "w") as f:
            json.dump(result, f)
        return None

    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the feedback UI")
    parser.add_argument("--prompt", default="I implemented the changes you requested.", help="The prompt to show to the user")
    parser.add_argument("--predefined-options", default="", help="Pipe-separated list of predefined options (|||)")
    parser.add_argument("--prompt-images", default="", help="Pipe-separated image paths to render in prompt area (|||)")
    parser.add_argument("--output-file", help="Path to save the feedback result as JSON")
    args = parser.parse_args()

    predefined_options = [opt for opt in args.predefined_options.split("|||") if opt] if args.predefined_options else None
    prompt_images = [p for p in args.prompt_images.split("|||") if p] if args.prompt_images else None
    
    result = feedback_ui(args.prompt, predefined_options, args.output_file, prompt_images=prompt_images)
    if result:
        print(f"\nFeedback received:\n{result['interactive_feedback']}")
    sys.exit(0)
