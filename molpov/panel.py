"""Parameter sidebar: every widget mutates the shared :class:`SceneParams`."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import sdl

STYLE_LABELS = {
    "ball_stick": "球棍模型",
    "spacefill": "空间填充",
    "wireframe": "线框",
}
PALETTE_LABELS = {"cpk": "CPK", "vmd": "VMD 青碳", "pastel": "粉彩",
                  "custom": "自定义"}
MATERIAL_LABELS = {
    "matte": "哑光",
    "standard": "标准",
    "glossy": "高光",
    "metal": "金属",
    "avogadro": "Avogadro 1.x",
}
LIGHT_LABELS = {"studio": "影棚三灯", "avogadro": "Avogadro 双灯（跟随相机）"}
PRESET_LABELS = {
    "studio": "影棚（默认）",
    "avogadro": "Avogadro 1.x",
    "custom": "自定义",
}
BACKGROUND_LABELS = {
    "studio": "影棚灰",
    "dark": "深色",
    "light": "纯白",
    "black": "纯黑",
    "custom": "自定义",
    "transparent": "透明",
}


class FloatRow(QWidget):
    """Slider + spin box editing one float value."""

    valueChanged = Signal(float)

    def __init__(self, minimum: float, maximum: float, value: float,
                 step: float = 0.01, decimals: int = 2, parent=None):
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(self._to_slider(value))
        layout.addWidget(self.slider, 1)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setValue(value)
        self.spin.setFixedWidth(74)
        layout.addWidget(self.spin, 0)

        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def _to_slider(self, value: float) -> int:
        span = self._max - self._min
        if span <= 0:
            return 0
        return int(round((value - self._min) / span * 1000))

    def _from_slider(self, raw: int) -> None:
        value = self._min + (self._max - self._min) * raw / 1000.0
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.valueChanged.emit(value)

    def _from_spin(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(value))
        self.slider.blockSignals(False)
        self.valueChanged.emit(value)

    def set_value(self, value: float) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(value))
        self.slider.blockSignals(False)


class ParameterPanel(QWidget):
    changed = Signal()
    resetViewRequested = Signal()
    finalRenderRequested = Signal()
    delayChanged = Signal(int)
    editColorsRequested = Signal()

    def __init__(self, params: sdl.SceneParams, preset: str = "studio",
                 parent=None):
        super().__init__(parent)
        self.params = params
        self._preset = preset
        self._building = True
        self._syncing = False

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        layout.addWidget(self._build_appearance())
        layout.addWidget(self._build_geometry())
        layout.addWidget(self._build_light())
        layout.addWidget(self._build_render())
        layout.addWidget(self._build_camera())
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._building = False

    # ----------------------------------------------------------------- build
    def _combo(self, labels: dict, current: str) -> QComboBox:
        combo = QComboBox()
        for key, label in labels.items():
            combo.addItem(label, key)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    def _group(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(10, 8, 10, 10)
        form.setSpacing(7)
        form.setLabelAlignment(Qt.AlignLeft)
        return box, form

    def _build_appearance(self) -> QGroupBox:
        box, form = self._group("外观")
        self.preset_combo = self._combo(PRESET_LABELS, self._preset)
        self.style_combo = self._combo(STYLE_LABELS, self.params.style)
        self.palette_combo = self._combo(PALETTE_LABELS, self.params.palette)
        self.material_combo = self._combo(MATERIAL_LABELS, self.params.material)
        self.background_combo = self._combo(BACKGROUND_LABELS, self.params.background)
        self.floor_check = QCheckBox("显示地面与投影")
        self.floor_check.setChecked(self.params.floor)
        self.hide_h_check = QCheckBox("隐藏氢原子")
        self.hide_h_check.setChecked(self.params.hide_hydrogens)
        self.colors_button = QPushButton("编辑自定义配色…")
        self.colors_button.clicked.connect(self.editColorsRequested.emit)
        self.bg_color_button = QPushButton("自定义背景色…")
        self.bg_color_button.clicked.connect(self._pick_background_color)

        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        self.style_combo.currentIndexChanged.connect(self._on_style)
        self.palette_combo.currentIndexChanged.connect(self._on_palette)
        self.material_combo.currentIndexChanged.connect(
            lambda: self._set("material", self.material_combo.currentData()))
        self.background_combo.currentIndexChanged.connect(
            lambda: self._set("background", self.background_combo.currentData()))
        self.floor_check.toggled.connect(lambda v: self._set("floor", bool(v)))
        self.hide_h_check.toggled.connect(
            lambda v: self._set("hide_hydrogens", bool(v)))

        form.addRow("风格预设", self.preset_combo)
        form.addRow("样式", self.style_combo)
        form.addRow("配色", self.palette_combo)
        form.addRow("", self.colors_button)
        form.addRow("材质", self.material_combo)
        form.addRow("背景", self.background_combo)
        form.addRow("", self.bg_color_button)
        form.addRow("", self.floor_check)
        form.addRow("", self.hide_h_check)
        return box

    def _build_geometry(self) -> QGroupBox:
        box, form = self._group("几何")
        self.atom_scale = FloatRow(0.05, 1.0, self.params.atom_scale, 0.01)
        self.bond_radius = FloatRow(0.0, 0.30, self.params.bond_radius, 0.005, 3)
        self.transparency = FloatRow(0.0, 0.85, self.params.transparency, 0.05)
        self.atom_scale.valueChanged.connect(self._on_atom_scale)
        self.bond_radius.valueChanged.connect(
            lambda v: self._set("bond_radius", float(v)))
        self.transparency.valueChanged.connect(
            lambda v: self._set("transparency", float(v)))
        form.addRow("原子半径", self.atom_scale)
        form.addRow("键半径", self.bond_radius)
        form.addRow("透明度", self.transparency)
        return box

    def _build_light(self) -> QGroupBox:
        box, form = self._group("光照")
        light = self.params.light
        self.light_combo = self._combo(LIGHT_LABELS, light.style)
        self.light_combo.currentIndexChanged.connect(
            lambda: self._set_light_mode(self.light_combo.currentData()))
        self.azimuth = FloatRow(0, 360, light.azimuth, 1, 0)
        self.elevation = FloatRow(-10, 85, light.elevation, 1, 0)
        self.key_intensity = FloatRow(0.0, 2.5, light.key_intensity, 0.05)
        self.fill_intensity = FloatRow(0.0, 1.5, light.fill_intensity, 0.05)
        self.softness = FloatRow(0.02, 1.0, light.softness, 0.01)
        self.headlight_check = QCheckBox("头灯（跟随相机）")
        self.headlight_check.setChecked(light.headlight)
        self.headlight_intensity = FloatRow(
            0.0, 1.5, light.headlight_intensity, 0.05)
        self.headlight_intensity.setEnabled(light.headlight)
        self.azimuth.valueChanged.connect(lambda v: self._set_light("azimuth", v))
        self.elevation.valueChanged.connect(lambda v: self._set_light("elevation", v))
        self.key_intensity.valueChanged.connect(
            lambda v: self._set_light("key_intensity", v))
        self.fill_intensity.valueChanged.connect(
            lambda v: self._set_light("fill_intensity", v))
        self.softness.valueChanged.connect(
            lambda v: self._set_light("softness", v))
        self.headlight_check.toggled.connect(self._on_headlight)
        self.headlight_intensity.valueChanged.connect(
            lambda v: self._set_light("headlight_intensity", v))
        form.addRow("模式", self.light_combo)
        form.addRow("", self.headlight_check)
        form.addRow("头灯强度", self.headlight_intensity)
        form.addRow("方位角", self.azimuth)
        form.addRow("高度角", self.elevation)
        form.addRow("主光强度", self.key_intensity)
        form.addRow("补光强度", self.fill_intensity)
        form.addRow("阴影柔化", self.softness)
        return box

    def _build_render(self) -> QGroupBox:
        box, form = self._group("渲染")
        self.width = FloatRow(240, 2400, self.params.width, 20, 0)
        self.width.spin.setSingleStep(20)
        self.radiosity_check = QCheckBox("全局光照 (radiosity)")
        self.radiosity_check.setChecked(self.params.radiosity)
        self.alpha_check = QCheckBox("透明背景 (PNG alpha)")
        self.alpha_check.setChecked(self.params.alpha)
        self.pack_check = QCheckBox("合并几何 union (更快)")
        self.pack_check.setChecked(self.params.pack_scene)
        self.full_check = QCheckBox("完整命令行 (Avogadro 同款)")
        self.full_check.setChecked(self.params.full_command)
        self.full_check.setToolTip("+Q9 +A +AM2 +UA，与 Avogadro 的 POV-Ray 调用一致")

        self.width.valueChanged.connect(
            lambda v: self._set("width", int(round(v))))
        self.radiosity_check.toggled.connect(
            lambda v: self._set("radiosity", bool(v)))
        self.alpha_check.toggled.connect(lambda v: self._set("alpha", bool(v)))
        self.pack_check.toggled.connect(
            lambda v: self._set("pack_scene", bool(v)))
        self.full_check.toggled.connect(
            lambda v: self._set("full_command", bool(v)))

        self.final_button = QPushButton("高质量渲染 (最终)")
        self.final_button.clicked.connect(self.finalRenderRequested.emit)

        self.aa_check = QCheckBox("抗锯齿 (高质量渲染)")
        self.aa_check.setChecked(self.params.antialias)
        self.aa_threshold = FloatRow(0.02, 0.6, self.params.aa_threshold, 0.01, 2)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 11)
        self.quality_spin.setValue(int(self.params.quality))
        self.maxtrace_spin = QSpinBox()
        self.maxtrace_spin.setRange(1, 30)
        self.maxtrace_spin.setValue(int(self.params.max_trace_level))

        self.aa_check.toggled.connect(
            lambda v: self._set("antialias", bool(v)))
        self.aa_threshold.valueChanged.connect(
            lambda v: self._set("aa_threshold", float(v)))
        self.quality_spin.valueChanged.connect(
            lambda v: self._set("quality", int(v)))
        self.maxtrace_spin.valueChanged.connect(
            lambda v: self._set("max_trace_level", int(v)))

        self.delay_combo = QComboBox()
        for label, ms in (("0.3 秒", 300), ("0.7 秒", 700),
                          ("1.5 秒", 1500), ("3 秒", 3000)):
            self.delay_combo.addItem(label, ms)
        self.delay_combo.setCurrentIndex(1)
        self.delay_combo.currentIndexChanged.connect(
            lambda: self.delayChanged.emit(int(self.delay_combo.currentData())))

        form.addRow("输出宽度", self.width)
        form.addRow("预览延迟", self.delay_combo)
        form.addRow("", self.aa_check)
        form.addRow("抗锯齿阈值", self.aa_threshold)
        form.addRow("渲染质量 (+Q)", self.quality_spin)
        form.addRow("追踪深度", self.maxtrace_spin)
        form.addRow("", self.radiosity_check)
        form.addRow("", self.alpha_check)
        form.addRow("", self.pack_check)
        form.addRow("", self.full_check)
        form.addRow("", self.final_button)
        return box

    def _build_camera(self) -> QGroupBox:
        box, form = self._group("相机")
        self.fov = FloatRow(15, 80, self.params.camera.fov, 1, 0)
        self.fov.valueChanged.connect(self._set_fov)
        self.reset_button = QPushButton("重置视角 / 自动构图")
        self.reset_button.clicked.connect(self.resetViewRequested.emit)
        form.addRow("视场角", self.fov)
        form.addRow("", self.reset_button)
        hint = QLabel("左键拖动旋转 · 右键拖动或滚轮缩放 · 双击复位")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7a8290; font-size: 11px;")
        form.addRow(hint)
        return box

    # --------------------------------------------------------------- events
    def _set(self, name: str, value) -> None:
        setattr(self.params, name, value)
        if name in ("style", "atom_scale", "bond_radius"):
            self._sync_geometry()
        self._emit()

    def _set_light(self, name: str, value) -> None:
        setattr(self.params.light, name, float(value))
        self._emit()

    def _set_light_mode(self, style: str) -> None:
        self.params.light.style = style
        if style == "avogadro" and self.params.floor:
            # Avogadro 1.x has no ground plane; drop it so the look matches.
            self.floor_check.setChecked(False)  # emits and updates params
            return
        self._emit()

    def _on_headlight(self, enabled: bool) -> None:
        self.params.light.headlight = bool(enabled)
        self.headlight_intensity.setEnabled(bool(enabled))
        self._emit()

    def _set_fov(self, value: float) -> None:
        self.params.camera.fov = float(value)
        self._emit()

    def _on_style(self) -> None:
        style = self.style_combo.currentData()
        self.params.style = style
        defaults = {"ball_stick": (0.28, 0.11), "spacefill": (1.0, 0.0),
                    "wireframe": (0.10, 0.05)}[style]
        self.params.atom_scale, self.params.bond_radius = defaults
        self._sync_geometry()
        self._emit()

    def _on_atom_scale(self, value: float) -> None:
        self.params.atom_scale = float(value)
        self._emit()

    def _on_palette(self) -> None:
        palette = self.palette_combo.currentData()
        self.params.palette = palette
        if palette == "custom":
            self.editColorsRequested.emit()
        self._emit()

    def _pick_background_color(self) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog

        r, g, b = self.params.background_color
        colour = QColorDialog.getColor(QColor.fromRgbF(r, g, b), self,
                                       "选择背景颜色")
        if not colour.isValid():
            return
        self.params.background_color = (colour.redF(), colour.greenF(),
                                        colour.blueF())
        self.params.background = "custom"
        index = self.background_combo.findData("custom")
        self.background_combo.blockSignals(True)
        self.background_combo.setCurrentIndex(max(0, index))
        self.background_combo.blockSignals(False)
        self._emit()

    def _sync_geometry(self) -> None:
        self.atom_scale.set_value(self.params.atom_scale)
        self.bond_radius.set_value(self.params.bond_radius)

    def _on_preset(self) -> None:
        name = self.preset_combo.currentData()
        if self._building or self._syncing or name == "custom":
            return
        sdl.apply_preset(self.params, name)
        self._sync_widgets()
        self.changed.emit()

    def _sync_widgets(self) -> None:
        """Reflect params into the widgets without firing their handlers."""
        self._syncing = True
        try:
            for combo, value in (
                (self.palette_combo, self.params.palette),
                (self.material_combo, self.params.material),
                (self.background_combo, self.params.background),
                (self.light_combo, self.params.light.style),
            ):
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                combo.blockSignals(False)
            self.floor_check.blockSignals(True)
            self.floor_check.setChecked(self.params.floor)
            self.floor_check.blockSignals(False)
            self.hide_h_check.blockSignals(True)
            self.hide_h_check.setChecked(self.params.hide_hydrogens)
            self.hide_h_check.blockSignals(False)
            self.headlight_check.blockSignals(True)
            self.headlight_check.setChecked(self.params.light.headlight)
            self.headlight_check.blockSignals(False)
            self.headlight_intensity.setEnabled(self.params.light.headlight)
            self.radiosity_check.blockSignals(True)
            self.radiosity_check.setChecked(self.params.radiosity)
            self.radiosity_check.blockSignals(False)
            self.alpha_check.blockSignals(True)
            self.alpha_check.setChecked(self.params.alpha)
            self.alpha_check.blockSignals(False)
            self.pack_check.blockSignals(True)
            self.pack_check.setChecked(self.params.pack_scene)
            self.pack_check.blockSignals(False)
            self.full_check.blockSignals(True)
            self.full_check.setChecked(self.params.full_command)
            self.full_check.blockSignals(False)
            self.aa_check.blockSignals(True)
            self.aa_check.setChecked(self.params.antialias)
            self.aa_check.blockSignals(False)
            self.quality_spin.blockSignals(True)
            self.quality_spin.setValue(int(self.params.quality))
            self.quality_spin.blockSignals(False)
            self.maxtrace_spin.blockSignals(True)
            self.maxtrace_spin.setValue(int(self.params.max_trace_level))
            self.maxtrace_spin.blockSignals(False)
            self.aa_threshold.set_value(self.params.aa_threshold)
            self.transparency.set_value(self.params.transparency)
            self.width.set_value(self.params.width)
            self.atom_scale.set_value(self.params.atom_scale)
            self.bond_radius.set_value(self.params.bond_radius)
            self.fov.set_value(self.params.camera.fov)
            self.azimuth.set_value(self.params.light.azimuth)
            self.elevation.set_value(self.params.light.elevation)
            self.key_intensity.set_value(self.params.light.key_intensity)
            self.fill_intensity.set_value(self.params.light.fill_intensity)
            self.softness.set_value(self.params.light.softness)
            self.headlight_intensity.set_value(
                self.params.light.headlight_intensity)
        finally:
            self._syncing = False

    def _emit(self) -> None:
        if self._building or self._syncing:
            return
        if self.preset_combo.currentData() != "custom":
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(
                max(0, self.preset_combo.findData("custom")))
            self.preset_combo.blockSignals(False)
        self.changed.emit()
