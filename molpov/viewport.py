"""Interactive (rasterised) viewport.

POV-Ray is an offline renderer, so dragging the molecule around has to happen
on the CPU here.  The projection mirrors the POV-Ray camera (same yaw, pitch,
zoom and field of view) which keeps the preview and the ray traced image
consistent.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import elements
from .sdl import SceneParams, camera_basis
from .structure import Structure, visible_atoms, visible_bonds

SPRITE = 96  # base sprite size in pixels


def _mix(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(
        int(c1.red() * (1 - t) + c2.red() * t),
        int(c1.green() * (1 - t) + c2.green() * t),
        int(c1.blue() * (1 - t) + c2.blue() * t),
    )


class MoleculeView(QWidget):
    cameraChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)
        self._structure: Optional[Structure] = None
        self._params = SceneParams()
        self._style = self._params.style
        self._sprites: Dict[Tuple[str, str], QPixmap] = {}
        self._last_mouse: Optional[QPointF] = None
        self._drag_button = Qt.NoButton
        self._drag_moved = False
        self._background = "studio"
        self._needs_fit = False
        self._user_zoomed = False
        self._hint = "打开一个 XYZ / PDB / MOL / SDF 文件开始"

    # ------------------------------------------------------------------ api
    def set_structure(self, structure: Optional[Structure]) -> None:
        self._structure = structure
        if structure is not None and len(structure):
            self._user_zoomed = False
            self._params.camera.zoom = self._fit_zoom()
            self._needs_fit = True
        self.update()

    def set_scene_params(self, params: SceneParams) -> None:
        style_changed = params.style != self._style
        self._params = params
        self._style = params.style
        self._background = params.background
        if style_changed and self._structure is not None:
            self._user_zoomed = False
            self._params.camera.zoom = self._fit_zoom()
        self.update()

    def set_hint(self, text: str) -> None:
        self._hint = text
        self.update()

    def reset_view(self) -> None:
        if self._structure is None:
            return
        self._user_zoomed = False
        self._params.camera.quaternion = None
        self._params.camera.yaw = 25.0
        self._params.camera.pitch = 18.0
        self._params.camera.zoom = self._fit_zoom()
        self.cameraChanged.emit()
        self.update()

    # -------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:
        self._last_mouse = event.position()
        self._drag_button = event.button()
        self._drag_moved = False
        self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._last_mouse = None
        self._drag_button = Qt.NoButton
        self.unsetCursor()
        if self._drag_moved:
            # Re-render only once the user stops dragging.
            self._drag_moved = False
            self.cameraChanged.emit()

    def mouseMoveEvent(self, event) -> None:
        if self._last_mouse is None or self._structure is None:
            return
        delta = event.position() - self._last_mouse
        self._last_mouse = event.position()
        cam = self._params.camera
        if self._drag_button == Qt.LeftButton:
            # Free trackball: rotate about the camera's own axes so the view
            # can tumble past the poles instead of clamping at ±89°.
            right, up, _ = camera_basis(cam)
            k = math.radians(0.35)
            step = _quat_mul(
                _quat_axis_angle(right, -delta.y() * k),
                _quat_axis_angle(up, -delta.x() * k),
            )
            base = cam.quaternion or (0.0, 0.0, 0.0, 1.0)
            cam.quaternion = _quat_normalize(_quat_mul(step, base))
        elif self._drag_button == Qt.RightButton:
            self._user_zoomed = True
            cam.zoom = max(1.1, min(14.0, cam.zoom * (1.0 - delta.y() * 0.004)))
        self._drag_moved = True
        self.update()

    def wheelEvent(self, event) -> None:
        if self._structure is None:
            return
        steps = event.angleDelta().y() / 120.0
        self._user_zoomed = True
        cam = self._params.camera
        cam.zoom = max(1.1, min(14.0, cam.zoom * math.exp(-steps * 0.12)))
        self.cameraChanged.emit()
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._structure is not None and not self._user_zoomed:
            self._params.camera.zoom = self._fit_zoom()
            self._needs_fit = False
            self.cameraChanged.emit()

    # ---------------------------------------------------------------- math
    def _fit_zoom(self) -> float:
        """Distance at which the molecule just fits the current orientation."""
        if self._structure is None:
            return 3.6
        params = self._params.for_style()
        fov = math.radians(max(10.0, params.camera.fov))
        tan_v = math.tan(fov / 2.0)
        aspect = self.width() / max(1, self.height())
        tan_h = tan_v * max(0.5, aspect)
        right, up, forward = camera_basis(params.camera)
        atoms = visible_atoms(self._structure, params.hide_hydrogens)
        if not atoms:
            return 3.6
        center = (
            sum(a.x for a in atoms) / len(atoms),
            sum(a.y for a in atoms) / len(atoms),
            sum(a.z for a in atoms) / len(atoms),
        )
        radius = max(0.6, max(math.dist((a.x, a.y, a.z), center) for a in atoms))
        needed = 0.0
        for atom in atoms:
            v = _sub(atom.pos, center)
            r = elements.vdw_radius(atom.element) * params.atom_scale
            du = abs(_dot(v, right)) + r
            dv = abs(_dot(v, up)) + r
            dw = _dot(v, forward)
            needed = max(needed, du / tan_h - dw, dv / tan_v - dw)
        return max(1.4, needed * 1.08 / radius)

    def _camera(self):
        st = self._structure
        atoms = visible_atoms(st, self._params.hide_hydrogens)
        if atoms:
            n = float(len(atoms))
            center = (
                sum(a.x for a in atoms) / n,
                sum(a.y for a in atoms) / n,
                sum(a.z for a in atoms) / n,
            )
            radius = max(
                0.6, max(math.dist((a.x, a.y, a.z), center) for a in atoms))
        else:
            center = st.center()
            radius = max(0.6, st.radius())
        cam = self._params.camera
        dist = max(0.2, radius * cam.zoom)
        right, up, forward = camera_basis(cam)
        eye = (center[0] - dist * forward[0],
               center[1] - dist * forward[1],
               center[2] - dist * forward[2])
        return eye, forward, right, up

    # -------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect()
        self._paint_background(painter, rect)
        if self._structure is None or not len(self._structure):
            self._paint_hint(painter, rect)
            painter.end()
            return
        self._paint_molecule(painter, rect)
        painter.end()

    def _paint_background(self, painter: QPainter, rect) -> None:
        gradient = QLinearGradient(0, 0, 0, rect.height())
        if self._background == "black":
            top, bottom = QColor(12, 12, 14), QColor(0, 0, 0)
        elif self._background == "dark":
            top, bottom = QColor(26, 32, 44), QColor(12, 14, 18)
        elif self._background == "light":
            top, bottom = QColor(255, 255, 255), QColor(236, 238, 242)
        elif self._background == "transparent":
            top, bottom = QColor(250, 250, 252), QColor(240, 241, 245)
        elif self._background == "custom":
            r, g, b = self._params.background_color
            base = QColor.fromRgbF(max(0.0, min(1.0, r)),
                                   max(0.0, min(1.0, g)),
                                   max(0.0, min(1.0, b)))
            top = base.lighter(108)
            bottom = base.darker(108)
        else:
            top, bottom = QColor(244, 246, 250), QColor(214, 219, 228)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillRect(rect, gradient)

    def _paint_hint(self, painter: QPainter, rect) -> None:
        painter.setPen(QColor(120, 128, 140))
        font = QFont()
        font.setPointSizeF(11)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, self._hint)

    def _paint_molecule(self, painter: QPainter, rect) -> None:
        st = self._structure
        eye, forward, right, up = self._camera()
        width, height = rect.width(), rect.height()
        fov = math.radians(max(10.0, self._params.camera.fov))
        focal = (height / 2.0) / math.tan(fov / 2.0)
        params = self._params.for_style()

        def project(pos) -> Tuple[float, float, float]:
            v = _sub(pos, eye)
            z = max(1e-3, _dot(v, forward))
            x = _dot(v, right)
            y = _dot(v, up)
            return (width / 2.0 + x * focal / z,
                    height / 2.0 - y * focal / z,
                    z)

        atoms = visible_atoms(st, params.hide_hydrogens)
        bonds = visible_bonds(st, params.hide_hydrogens)
        projected = {atom.index: project(atom.pos) for atom in atoms}
        depths = sorted(p[2] for p in projected.values())
        median = depths[len(depths) // 2] if depths else 1.0

        opacity = max(0.15, 1.0 - params.transparency)
        if opacity < 0.999:
            painter.setOpacity(opacity)

        # ---- bonds first: they belong behind the atom sprites
        bond_r = params.bond_radius
        if params.style != "spacefill" and bond_r > 0:
            for bond in bonds:
                a, b = st.atoms[bond.a], st.atoms[bond.b]
                pa = project(a.pos)
                pb = project(b.pos)
                mid = ((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
                pm = project(mid)
                color_a = elements.color(a.element, params.palette,
                                         params.custom_colors)
                color_b = elements.color(b.element, params.palette,
                                         params.custom_colors)
                order = max(1, min(3, int(round(bond.order))))
                if order == 1:
                    self._draw_bond(painter, pa, pm, pb, color_a, color_b,
                                    bond_r, focal, median)
                    continue
                dx, dy = pb[0] - pa[0], pb[1] - pa[1]
                length = math.hypot(dx, dy) or 1.0
                nx, ny = -dy / length, dx / length
                radius_px = max(0.8, bond_r * focal / ((pa[2] + pb[2]) / 2.0))
                step = max(2.4, radius_px * 2.4)
                for i in range(order):
                    off = (i - (order - 1) / 2.0) * step
                    shift = lambda p, ox=nx * off, oy=ny * off: (
                        p[0] + ox, p[1] + oy, p[2])
                    self._draw_bond(painter, shift(pa), shift(pm), shift(pb),
                                    color_a, color_b, bond_r, focal, median,
                                    width_scale=0.6)

        # ---- atoms sorted far to near
        order = sorted(atoms, key=lambda atom: -projected[atom.index][2])
        for atom in order:
            sx, sy, z = projected[atom.index]
            radius3d = elements.vdw_radius(atom.element) * params.atom_scale
            rpx = radius3d * focal / z
            if rpx < 0.6:
                continue
            fade = _depth_fade(z, median)
            sprite = self._sprite(atom.element, params)
            size = rpx * 2.0
            if fade > 0.01:
                sprite = self._faded_sprite(atom.element, params, fade)
            painter.drawPixmap(
                QRectF(sx - rpx, sy - rpx, size, size), sprite, QRectF(sprite.rect())
            )

        self._paint_scale_bar(painter, rect, focal, median)
        painter.setOpacity(1.0)

    def _draw_bond(self, painter, pa, pm, pb, color_a, color_b, bond_r,
                   focal, median, width_scale: float = 1.0) -> None:
        radius_px = max(0.8, bond_r * focal / ((pa[2] + pb[2]) / 2.0))
        fade = _depth_fade(max(pa[2], pb[2]), median)
        pen = QPen(_mix(QColor(*color_a), QColor(210, 214, 222), fade))
        pen.setWidthF(radius_px * 2.0 * width_scale)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(pa[0], pa[1]), QPointF(pm[0], pm[1]))
        pen.setColor(_mix(QColor(*color_b), QColor(210, 214, 222), fade))
        painter.setPen(pen)
        painter.drawLine(QPointF(pm[0], pm[1]), QPointF(pb[0], pb[1]))

    def _sprite(self, symbol: str, params: SceneParams) -> QPixmap:
        rgb = elements.color(symbol, params.palette, params.custom_colors)
        key = (symbol, rgb)
        cached = self._sprites.get(key)  # type: ignore[arg-type]
        if cached is not None:
            return cached
        pixmap = _make_sprite(QColor(*rgb))
        self._sprites[key] = pixmap  # type: ignore[index]
        return pixmap

    def _faded_sprite(self, symbol: str, params: SceneParams,
                      fade: float) -> QPixmap:
        rgb = elements.color(symbol, params.palette, params.custom_colors)
        key = (symbol, rgb, round(fade, 2))
        cached = self._sprites.get(key)  # type: ignore[arg-type]
        if cached is not None:
            return cached
        base = QColor(*rgb)
        pixmap = _make_sprite(_mix(base, QColor(210, 214, 222), fade))
        self._sprites[key] = pixmap  # type: ignore[index]
        return pixmap

    def _paint_scale_bar(self, painter, rect, focal, median) -> None:
        length_px = focal / max(1e-3, median)  # 1 Angstrom at the median depth
        if length_px < 8:
            return
        painter.setPen(QPen(QColor(90, 96, 108, 160), 1.4))
        x0, y0 = 16.0, rect.height() - 20.0
        painter.drawLine(QPointF(x0, y0), QPointF(x0 + length_px, y0))
        painter.drawLine(QPointF(x0, y0 - 4), QPointF(x0, y0 + 4))
        painter.drawLine(QPointF(x0 + length_px, y0 - 4),
                         QPointF(x0 + length_px, y0 + 4))
        font = QFont()
        font.setPointSizeF(8)
        painter.setFont(font)
        painter.setPen(QColor(90, 96, 108, 200))
        painter.drawText(QPointF(x0 + length_px + 6, y0 + 4), "1 Angstrom")


def _make_sprite(base: QColor) -> QPixmap:
    pixmap = QPixmap(SPRITE, SPRITE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    radius = SPRITE / 2.0 - 1.0
    gradient = QRadialGradient(
        QPointF(SPRITE * 0.36, SPRITE * 0.32), radius * 1.45,
        QPointF(SPRITE * 0.36, SPRITE * 0.32),
    )
    gradient.setColorAt(0.0, base.lighter(175))
    gradient.setColorAt(0.45, base)
    gradient.setColorAt(0.86, base.darker(160))
    gradient.setColorAt(1.0, base.darker(230))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QRectF(1, 1, SPRITE - 2, SPRITE - 2))
    highlight = QRadialGradient(
        QPointF(SPRITE * 0.34, SPRITE * 0.28), radius * 0.34)
    highlight.setColorAt(0.0, QColor(255, 255, 255, 200))
    highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(highlight)
    painter.drawEllipse(QRectF(1, 1, SPRITE - 2, SPRITE - 2))
    painter.end()
    return pixmap


def _depth_fade(z: float, median: float) -> float:
    if median <= 0:
        return 0.0
    return max(0.0, min(0.55, (z / median - 1.0) * 0.35))


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(v):
    length = math.sqrt(_dot(v, v)) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _quat_axis_angle(axis, angle):
    ax, ay, az = _normalize(axis)
    half = angle / 2.0
    sin_half = math.sin(half)
    return (ax * sin_half, ay * sin_half, az * sin_half, math.cos(half))


def _quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _quat_normalize(q):
    length = math.sqrt(sum(v * v for v in q)) or 1.0
    return tuple(v / length for v in q)
