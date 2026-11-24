"""Qt main window: interactive viewport on the left, POV-Ray preview below."""

from __future__ import annotations

import copy
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import elements, sdl
from .panel import ParameterPanel
from .render import (
    Quality,
    Renderer,
    RenderOutcome,
    final_quality,
    full_command_quality,
    preview_quality,
)
from .structure import Structure, read_structure
from .viewport import MoleculeView

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "wsl_server.py")


@dataclass
class RenderJob:
    structure: Structure
    params: sdl.SceneParams
    quality: Quality
    tag: int
    final: bool = False


@dataclass
class RawPovJob:
    pov_path: str
    width: int
    height: int
    text: Optional[str] = None
    alpha: bool = True


class RenderWorker(QObject):
    done = Signal(object, object)  # (RenderJob, RenderOutcome)
    raw_done = Signal(object, object)  # (RawPovJob, RenderOutcome)

    def __init__(self, renderer: Renderer):
        super().__init__()
        self._renderer = renderer

    @Slot(object)
    def render_job(self, job: RenderJob) -> None:
        outcome = self._renderer.render(job.structure, job.params, job.quality)
        self.done.emit(job, outcome)

    @Slot(object)
    def render_raw_pov(self, job: RawPovJob) -> None:
        if job.text is not None:
            outcome = self._renderer.render_pov_text(
                job.text, job.width, job.height, job.alpha)
        else:
            outcome = self._renderer.render_pov_file(
                job.pov_path, job.width, job.height, job.alpha)
        self.raw_done.emit(job, outcome)

    @Slot()
    def shutdown(self) -> None:
        self._renderer.close()


class _ImageCanvas(QWidget):
    """Letterboxed, aspect-preserving view of the last rendered frame."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(27, 31, 39))
        if self._pixmap.isNull():
            painter.setPen(QColor(111, 120, 133))
            font = QFont()
            font.setPointSizeF(11)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "POV-Ray 预览将显示在这里")
            painter.end()
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        size = self._pixmap.size().scaled(self.size(), Qt.KeepAspectRatio)
        if os.environ.get("MOLPOV_DEBUG"):
            print(f"[canvas] widget={self.size().width()}x{self.size().height()} "
                  f"pixmap={self._pixmap.width()}x{self._pixmap.height()} "
                  f"scaled={size.width()}x{size.height()}")
        target = (
            (self.width() - size.width()) // 2,
            (self.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )
        if self._pixmap.hasAlphaChannel():
            # Checkerboard so a transparent background still shows the frame.
            tile = 12
            painter.save()
            painter.setClipRect(target[0], target[1], target[2], target[3])
            for y in range(target[1], target[1] + target[3], tile):
                for x in range(target[0], target[0] + target[2], tile):
                    light = ((x // tile) + (y // tile)) % 2 == 0
                    painter.fillRect(x, y, tile, tile,
                                     QColor(62, 67, 79) if light
                                     else QColor(47, 51, 61))
            painter.restore()
        painter.drawPixmap(*target, self._pixmap)
        painter.setPen(QColor(96, 104, 118))
        painter.drawRect(target[0], target[1], target[2] - 1, target[3] - 1)
        painter.end()


class PreviewPane(QWidget):
    """Shows the last ray traced frame plus a short status line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.canvas = _ImageCanvas()
        layout.addWidget(self.canvas, 1)

        bar = QWidget()
        bar.setStyleSheet("background: #232833;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 3, 10, 3)
        self.status = QLabel("就绪")
        self.status.setStyleSheet("color: #c7ccd6; font-size: 11px;")
        bar_layout.addWidget(self.status, 1)
        self.timing = QLabel("")
        self.timing.setStyleSheet("color: #8a93a1; font-size: 11px;")
        bar_layout.addWidget(self.timing, 0)
        layout.addWidget(bar)

    def show_pixmap(self, pixmap: QPixmap, note: str = "") -> None:
        self.canvas.set_pixmap(pixmap)
        if note:
            self.status.setText(note)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_timing(self, text: str) -> None:
        self.timing.setText(text)


class _ColorButton(QPushButton):
    """A colour swatch that opens QColorDialog when clicked."""

    def __init__(self, colour: QColor, parent=None):
        super().__init__(parent)
        self._colour = QColor(colour)
        self.setFixedSize(56, 22)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._colour.name()};"
            "border: 1px solid #7a8290; border-radius: 3px;"
        )

    def _pick(self) -> None:
        colour = QColorDialog.getColor(self._colour, self, "选择元素颜色")
        if colour.isValid():
            self._colour = colour
            self._refresh()

    def color(self) -> QColor:
        return self._colour


class ColorSchemeDialog(QDialog):
    """Per-element colour overrides for the current structure."""

    def __init__(self, symbols, initial, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义元素配色")
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(6)
        self._buttons = {}
        for row, symbol in enumerate(symbols):
            grid.addWidget(QLabel(symbol), row, 0)
            button = _ColorButton(initial.get(symbol, QColor("#909090")))
            self._buttons[symbol] = button
            grid.addWidget(button, row, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset = buttons.addButton("重置为 CPK", QDialogButtonBox.ResetRole)
        reset.clicked.connect(self._reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        grid.addWidget(buttons, len(symbols), 0, 1, 2)

    def _reset(self) -> None:
        for symbol, button in self._buttons.items():
            button._colour = QColor(*elements.color(symbol, "cpk"))
            button._refresh()

    def colors(self) -> dict:
        return {
            symbol: (button.color().red(), button.color().green(),
                     button.color().blue())
            for symbol, button in self._buttons.items()
        }


class MainWindow(QMainWindow):
    requestRender = Signal(object)
    requestRawPov = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MolPOV — 分子可视化 · POV-Ray 实时预览")
        self.resize(1280, 820)

        self.params = sdl.SceneParams()
        sdl.apply_preset(self.params, "avogadro")
        self.structure: Optional[Structure] = None
        self._tag = 0
        self._latest = 0
        self._busy = False
        self._busy_final = False
        self._pending: Optional[RenderJob] = None
        self._last_outcome: Optional[RenderOutcome] = None
        self._last_final: Optional[RenderOutcome] = None
        self._raw_pov_path: Optional[str] = None
        self._raw_pov_text: Optional[str] = None
        self._raw_baseline: Optional[tuple] = None
        self._settings = QSettings("MolPOV", "MolPOV")
        self._export_pov_with_image = self._settings.value(
            "export_pov_with_image", False, type=bool)
        self._working_dir = self._settings.value(
            "working_directory", "", type=str)
        self._povray_path = self._settings.value(
            "povray_path", "", type=str)
        self._auto_preview = False

        self.viewport = MoleculeView()
        self.panel = ParameterPanel(self.params, preset="avogadro")
        self.preview = PreviewPane()

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.panel)
        side.setMaximumWidth(372)
        side.setMinimumWidth(344)

        horizontal = QSplitter(Qt.Horizontal)
        horizontal.addWidget(self.viewport)
        horizontal.addWidget(side)
        horizontal.setStretchFactor(0, 1)
        horizontal.setStretchFactor(1, 0)
        horizontal.setSizes([880, 360])

        vertical = QSplitter(Qt.Vertical)
        vertical.addWidget(horizontal)
        vertical.addWidget(self.preview)
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 2)
        vertical.setSizes([460, 360])
        self.setCentralWidget(vertical)

        self._build_actions()
        self._build_menus()
        self._build_statusbar()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(700)
        self._debounce.timeout.connect(self._fire_preview)

        self.panel.changed.connect(self._on_params_changed)
        self.panel.resetViewRequested.connect(self._reset_view)
        self.panel.finalRenderRequested.connect(self._render_final)
        self.panel.delayChanged.connect(self._debounce.setInterval)
        self.panel.editColorsRequested.connect(self._edit_colors)
        self.viewport.cameraChanged.connect(self._on_camera_changed)

        self.renderer = Renderer(SCRIPT, exe_path=self._povray_path or None)
        self._thread = QThread(self)
        self.worker = RenderWorker(self.renderer)
        self.worker.moveToThread(self._thread)
        self.requestRender.connect(self.worker.render_job)
        self.requestRawPov.connect(self.worker.render_raw_pov)
        self.worker.done.connect(self._on_rendered)
        self.worker.raw_done.connect(self._on_raw_pov_rendered)
        self._thread.start()

        self._detect_backend()

    # ------------------------------------------------------------- toolbar
    def _build_actions(self) -> None:
        self.open_action = QAction("打开结构", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self.open_dialog)

        self.export_action = QAction("导出 POV 场景…", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.export_action.triggered.connect(self.export_pov)

        self.save_png_action = QAction("保存图片…", self)
        self.save_png_action.setShortcut(QKeySequence.Save)
        self.save_png_action.setEnabled(False)
        self.save_png_action.triggered.connect(self._save_png)

        self.quit_action = QAction("退出", self)
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.close)

        self.auto_action = QAction("自动渲染", self)
        self.auto_action.setCheckable(True)
        self.auto_action.setChecked(self._auto_preview)
        self.auto_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.auto_action.setToolTip("参数或视角停下后自动出 POV-Ray 预览")
        self.auto_action.toggled.connect(self._toggle_auto)

        self.export_pov_with_image_action = QAction("导出图片时同时导出 POV", self)
        self.export_pov_with_image_action.setCheckable(True)
        self.export_pov_with_image_action.setChecked(self._export_pov_with_image)
        self.export_pov_with_image_action.toggled.connect(
            self._toggle_export_pov_with_image)

        self.working_dir_action = QAction("设置工作目录…", self)
        self.working_dir_action.triggered.connect(self._choose_working_dir)

        self.povray_path_action = QAction("设置 POV-Ray 路径…", self)
        self.povray_path_action.triggered.connect(self._choose_povray_path)
        self.povray_path_action.setToolTip(self._povray_path or "自动检测")

        self.povray_auto_action = QAction("自动检测 POV-Ray", self)
        self.povray_auto_action.triggered.connect(self._auto_detect_povray)
        self.povray_auto_action.setEnabled(bool(self._povray_path))

        self.open_workdir_action = QAction("在文件管理器中打开工作目录", self)
        self.open_workdir_action.triggered.connect(self._open_working_dir)
        self.open_workdir_action.setEnabled(bool(self._working_dir))

        self.render_action = QAction("渲染当前预览", self)
        self.render_action.setShortcut(QKeySequence("F5"))
        self.render_action.triggered.connect(lambda: self._fire_preview(force=True))

        self.final_action = QAction("高质量渲染", self)
        self.final_action.setShortcut(QKeySequence("Ctrl+Return"))
        self.final_action.triggered.connect(self._render_final)

        self.pov_action = QAction("导入 POV 并渲染…", self)
        self.pov_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.pov_action.setToolTip("读取 .pov 的原子与相机，在界面里复现后继续调参")
        self.pov_action.triggered.connect(self._import_pov)

        self.raw_pov_action = QAction("原样渲染 Avogadro POV…", self)
        self.raw_pov_action.setToolTip(
            "不解析几何、不重建：直接用 Avogadro 完整命令行渲染选中的 .pov；"
            "相机/背景/尺寸可调，改完按 Ctrl+Enter 重新渲染")
        self.raw_pov_action.triggered.connect(self._render_raw_pov)

        self.reset_action = QAction("重置视角 / 自动构图", self)
        self.reset_action.setShortcut(QKeySequence("Ctrl+0"))
        self.reset_action.triggered.connect(self._reset_view)

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction(self.open_action)
        file_menu.addSeparator()
        file_menu.addAction(self.export_action)
        file_menu.addAction(self.save_png_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        render_menu = menubar.addMenu("渲染(&R)")
        render_menu.addAction(self.auto_action)  # 自动渲染开关
        render_menu.addSeparator()
        render_menu.addAction(self.pov_action)
        render_menu.addAction(self.raw_pov_action)
        render_menu.addAction(self.render_action)
        render_menu.addAction(self.final_action)

        view_menu = menubar.addMenu("视图(&V)")
        view_menu.addAction(self.reset_action)

        settings_menu = menubar.addMenu("设置(&S)")
        settings_menu.addAction(self.povray_path_action)
        settings_menu.addAction(self.povray_auto_action)
        settings_menu.addSeparator()
        settings_menu.addAction(self.working_dir_action)
        settings_menu.addAction(self.open_workdir_action)
        settings_menu.addSeparator()
        settings_menu.addAction(self.export_pov_with_image_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于 MolPOV", self)
        about_action.triggered.connect(self._about)
        help_menu.addAction(about_action)

    def _about(self) -> None:
        from . import __version__

        QMessageBox.about(
            self, "关于 MolPOV",
            f"MolPOV {__version__}\n\n分子可视化 + POV-Ray 预览。\n"
            f"渲染后端：{self.renderer.describe()}",
        )

    def _build_statusbar(self) -> None:
        self.backend_label = QLabel("检测 POV-Ray…")
        self.statusBar().addPermanentWidget(self.backend_label)
        self.statusBar().showMessage("打开一个分子文件开始")

    # --------------------------------------------------------------- logic
    def _detect_backend(self) -> None:
        probe = Renderer(SCRIPT, exe_path=self._povray_path or None)
        name = probe.backend_name()
        text = {"native": "POV-Ray 控制台", "wsl": "POV-Ray (WSL)",
                "gui": "POV-Ray (本机)", "none": "未找到 POV-Ray"}[name]
        detail = probe.describe()
        self.backend_label.setText(text)
        self.backend_label.setToolTip(detail)
        if name == "none":
            self.statusBar().showMessage(
                "未找到 POV-Ray：请安装 POV-Ray 3.7 控制台版，或在 WSL 中 apt install povray"
            )

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开分子结构", self._default_dir(),
            "分子结构 (*.xyz *.pdb *.mol *.sdf *.pov);;所有文件 (*)")
        if path:
            self.load(path)

    def load(self, path: str) -> bool:
        try:
            structure = read_structure(path)
        except Exception as exc:  # noqa: BLE001 - show a readable message
            QMessageBox.warning(self, "无法读取结构", f"{path}\n\n{exc}")
            return False
        self.structure = structure
        self._raw_pov_path = None
        self.viewport.set_structure(structure)
        self.viewport.set_scene_params(self.params)
        self.viewport.reset_view()
        self.statusBar().showMessage(
            f"{os.path.basename(path)} — {structure.summary()}"
            + ("" if self._auto_preview else "  ·  自动渲染已关闭，按 F5 出图"))
        self._on_params_changed()
        return True

    def _toggle_auto(self, enabled: bool) -> None:
        self._auto_preview = enabled
        if enabled:
            self._on_params_changed()
            self.statusBar().showMessage("自动渲染已开启")
        else:
            self.statusBar().showMessage("自动渲染已关闭（F5 手动渲染）")

    def _toggle_export_pov_with_image(self, enabled: bool) -> None:
        self._export_pov_with_image = bool(enabled)
        self._settings.setValue("export_pov_with_image", bool(enabled))
        self.statusBar().showMessage(
            "设置已保存：保存图片时同时导出 POV" if enabled
            else "设置已保存：保存图片时不自动导出 POV")

    def _default_dir(self) -> str:
        if self._working_dir and os.path.isdir(self._working_dir):
            return self._working_dir
        return ""

    def _choose_povray_path(self) -> None:
        start = self._povray_path or self._default_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 POV-Ray 可执行文件", start,
            "可执行文件 (*.exe);;所有文件 (*)")
        if not path:
            return
        self._povray_path = path
        self._settings.setValue("povray_path", path)
        self.povray_path_action.setToolTip(path)
        self.povray_auto_action.setEnabled(True)
        self._restart_renderer()
        self._detect_backend()
        self.statusBar().showMessage(f"POV-Ray 路径已设为 {path}")

    def _auto_detect_povray(self) -> None:
        self._povray_path = ""
        self._settings.setValue("povray_path", "")
        self.povray_path_action.setToolTip("自动检测")
        self.povray_auto_action.setEnabled(False)
        self._restart_renderer()
        self._detect_backend()
        self.statusBar().showMessage("已恢复 POV-Ray 自动检测")

    def _restart_renderer(self) -> None:
        old_worker = getattr(self, "worker", None)
        if old_worker is not None:
            for signal in (self.requestRender, self.requestRawPov):
                try:
                    signal.disconnect(old_worker)
                except (RuntimeError, TypeError):
                    pass
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        try:
            self.renderer.close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
        self.renderer = Renderer(SCRIPT, exe_path=self._povray_path or None)
        self.worker = RenderWorker(self.renderer)
        self._thread = QThread(self)
        self.worker.moveToThread(self._thread)
        self.requestRender.connect(self.worker.render_job)
        self.requestRawPov.connect(self.worker.render_raw_pov)
        self.worker.done.connect(self._on_rendered)
        self.worker.raw_done.connect(self._on_raw_pov_rendered)
        self._thread.start()
        self._busy = False
        self._pending = None

    def _choose_working_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择工作目录", self._default_dir())
        if not directory:
            return
        self._working_dir = directory
        self._settings.setValue("working_directory", directory)
        self.working_dir_action.setToolTip(directory)
        self.open_workdir_action.setEnabled(True)
        self.statusBar().showMessage(f"工作目录：{directory}")

    def _open_working_dir(self) -> None:
        if self._working_dir and os.path.isdir(self._working_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._working_dir))

    def _on_params_changed(self) -> None:
        self.viewport.set_scene_params(self.params)
        self.viewport.update()
        if self._auto_preview and self.structure is not None:
            self._debounce.start()

    def _on_camera_changed(self) -> None:
        if self._auto_preview and self.structure is not None:
            self._debounce.start()

    def _reset_view(self) -> None:
        self.viewport.reset_view()
        self._on_camera_changed()

    def _edit_colors(self) -> None:
        if self.structure is not None and len(self.structure):
            symbols = list(self.structure.elements)
        else:
            symbols = ["H", "C", "N", "O", "S", "P", "F", "Cl", "Br", "I"]
        initial = {
            symbol: QColor(*elements.color(symbol, self.params.palette,
                                           self.params.custom_colors))
            for symbol in symbols
        }
        dialog = ColorSchemeDialog(symbols, initial, self)
        if dialog.exec():
            self.params.custom_colors = dialog.colors()
            self.params.palette = "custom"
        elif self.params.palette == "custom" and not self.params.custom_colors:
            # "自定义" was picked in the combo but the dialog was cancelled.
            self.params.palette = "cpk"
        else:
            return
        index = self.panel.palette_combo.findData(self.params.palette)
        self.panel.palette_combo.blockSignals(True)
        self.panel.palette_combo.setCurrentIndex(max(0, index))
        self.panel.palette_combo.blockSignals(False)
        self.viewport.set_scene_params(self.params)
        self.viewport.update()
        self._on_params_changed()

    # ------------------------------------------------------------- renders
    def _fire_preview(self, force: bool = False) -> None:
        if self._raw_pov_path:
            # In "raw POV" mode the preview always re-renders that file.
            if force:
                self._render_raw_again()
            return
        if self.structure is None:
            return
        if not force and not self._auto_preview:
            return
        self._submit(final=False)

    def _raw_state(self) -> tuple:
        cam = self.params.camera
        return (
            round(cam.yaw, 2), round(cam.pitch, 2), round(cam.zoom, 4),
            round(cam.fov, 2), self.params.background,
            tuple(round(v, 4) for v in self.params.background_color),
            bool(self.params.alpha), int(self.params.width),
        )

    def _render_raw_again(self) -> None:
        if not self._raw_pov_path:
            return
        width = max(320, int(self.params.width))
        height = max(240, int(round(width * 0.75)))
        text = None
        if self._raw_pov_text and self._raw_state() != self._raw_baseline:
            # Only patch when the user actually changed camera/background/size,
            # so an untouched re-render stays byte-identical to the file.
            from dataclasses import replace as dataclass_replace

            from . import povparse

            snapshot = dataclass_replace(self.params, width=width,
                                         height=height)
            text = povparse.patch_scene(self._raw_pov_text, self.structure,
                                        snapshot)
            note = "已应用当前相机/背景"
        else:
            note = "未改动场景"
        self.statusBar().showMessage(
            f"正在渲染 {os.path.basename(self._raw_pov_path)} … {note}")
        self.requestRawPov.emit(
            RawPovJob(self._raw_pov_path, width, height, text,
                      bool(self.params.alpha)))

    def _submit(self, final: bool,
                quality_override: Optional[Quality] = None) -> None:
        assert self.structure is not None
        self._tag += 1
        self._latest = self._tag
        if quality_override is not None:
            quality = quality_override
        elif self.params.full_command:
            width = max(240, int(self.params.width))
            height = int(round(width * 0.75))
            quality = full_command_quality(width, height)
        elif final:
            quality = final_quality(
                max(240, int(self.params.width)), 4.0 / 3.0,
                antialias=(self.params.aa_threshold if self.params.antialias
                           else 0.0),
                quality=int(self.params.quality),
            )
        else:
            # Same output size and 4:3 framing as the final render, so the
            # preview is a true WYSIWYG of the exported PNG.
            quality = preview_quality(self.params.width, 4.0 / 3.0)
            quality = Quality(
                quality.label, quality.width, quality.height,
                quality.antialias, quality.aa_method, quality.radiosity,
                quality.extra + (f"+Q{int(self.params.quality)}",), quality.full,
            )
        job = RenderJob(
            structure=self.structure,
            params=copy.deepcopy(self.params),
            quality=quality,
            tag=self._tag,
            final=final,
        )
        if self._busy:
            self._pending = job
            # Drop the stale frame instead of queueing behind it, unless a
            # high-quality render is running (that one should finish).
            if not self._busy_final:
                self.renderer.cancel()
        else:
            self._dispatch(job)

    def _dispatch(self, job: RenderJob) -> None:
        self._busy = True
        self._busy_final = job.final
        self.preview.set_status(
            "正在渲染…" if job.final else "正在渲染预览…")
        self.preview.set_timing(f"{job.quality.width}×{job.quality.height}")
        self.requestRender.emit(job)

    @Slot(object, object)
    def _on_rendered(self, job: RenderJob, outcome: RenderOutcome) -> None:
        self._busy = False
        self._busy_final = False
        current = job.tag == self._latest
        cancelled = outcome.error == "cancelled"
        if outcome.ok and current:
            image = QImage.fromData(outcome.png, "PNG")
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                note = (f"{job.quality.width}×{job.quality.height} · "
                        f"{outcome.backend} · {outcome.elapsed_ms:.0f} ms")
                self.preview.show_pixmap(pixmap, note if not job.final else
                                         f"最终渲染 · {note}")
                self._last_outcome = outcome
                if job.final:
                    self._last_final = outcome
                    self.save_png_action.setEnabled(True)
                    self.statusBar().showMessage("高质量渲染完成")
        elif not outcome.ok and current and not cancelled:
            self.preview.set_status(f"渲染失败：{outcome.error[:160]}")
            self.preview.set_timing("")
        if self._pending is not None:
            pending = self._pending
            self._pending = None
            self._dispatch(pending)

    def _render_final(self) -> None:
        if self._raw_pov_path:
            # Stay in the external scene instead of falling back to the
            # structure that happens to be loaded.
            self._render_raw_again()
            return
        if self.structure is None:
            return
        self._submit(final=True)

    def _apply_parsed_scene(self, scene) -> None:
        """Mirror a parsed .pov into the structure, camera and look controls."""
        from . import povparse

        self.structure = scene.structure
        self.viewport.set_structure(scene.structure)

        hint = povparse.camera_hint(scene)
        if hint:
            cam = self.params.camera
            cam.quaternion = None
            cam.yaw, cam.pitch = hint["yaw"], hint["pitch"]
            cam.zoom, cam.fov = hint["zoom"], hint["fov"]
        if scene.background is not None:
            self.params.background = "custom"
            self.params.background_color = scene.background
        if scene.avogadro_finish:
            self.params.material = "avogadro"
        if scene.parallel_lights >= 2:
            self.params.light.style = "avogadro"
            self.params.floor = False

        # Imported geometry is re-coloured from the inferred elements.
        self.params.palette = "cpk"
        self.params.custom_colors = {}
        # Reproduce with Avogadro's full command line by default.
        self.params.full_command = True
        self.panel.preset_combo.blockSignals(True)
        self.panel.preset_combo.setCurrentIndex(
            max(0, self.panel.preset_combo.findData("custom")))
        self.panel.preset_combo.blockSignals(False)
        self.panel._sync_widgets()
        self.viewport.set_scene_params(self.params)
        self.viewport.update()
        self._on_params_changed()

    def _import_pov(self) -> None:
        """Read a .pov, rebuild the scene in the UI and render it here."""
        from . import povparse

        pov_path, _ = QFileDialog.getOpenFileName(
            self, "导入 POV-Ray 场景", self._default_dir(),
            "POV-Ray 场景 (*.pov);;所有文件 (*)")
        if not pov_path:
            return
        try:
            with open(pov_path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            scene = povparse.read_pov_scene(text, pov_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法读取 POV 场景", str(exc))
            return

        self._raw_pov_path = None
        self._apply_parsed_scene(scene)
        self._submit(final=False, quality_override=full_command_quality(1024, 768))
        self.statusBar().showMessage(
            f"已导入 {os.path.basename(pov_path)}：{scene.structure.summary()}"
            "  ·  已按 Avogadro 完整命令行复现，可继续调整参数")

    def _render_raw_pov(self) -> None:
        pov_path, _ = QFileDialog.getOpenFileName(
            self, "原样渲染 Avogadro POV", self._default_dir(),
            "POV-Ray 场景 (*.pov);;所有文件 (*)")
        if not pov_path:
            return
        self._raw_pov_path = pov_path
        try:
            with open(pov_path, "r", encoding="utf-8", errors="replace") as handle:
                self._raw_pov_text = handle.read()
        except OSError as exc:
            QMessageBox.warning(self, "无法读取 POV 场景", str(exc))
            return
        width = max(320, int(self.params.width))
        height = max(240, int(round(width * 0.75)))
        self.statusBar().showMessage(
            f"正在原样渲染 {os.path.basename(pov_path)} …")
        self.requestRawPov.emit(
            RawPovJob(pov_path, width, height, None, bool(self.params.alpha)))

    @Slot(object, object)
    def _on_raw_pov_rendered(self, job: "RawPovJob",
                             outcome: RenderOutcome) -> None:
        if not outcome.ok:
            self._raw_pov_path = None
            QMessageBox.warning(self, "POV-Ray 渲染失败", outcome.error)
            self.statusBar().showMessage("POV-Ray 渲染失败")
            return
        # Mirror the parsed geometry into the viewport so both panes show the
        # same molecule; the preview itself stays the untouched render.
        note = "视口已载入解析结构"
        try:
            from . import povparse

            with open(job.pov_path, "r", encoding="utf-8",
                      errors="replace") as handle:
                scene = povparse.read_pov_scene(handle.read(), job.pov_path)
            self._apply_parsed_scene(scene)
            self._raw_baseline = self._raw_state()
            note = f"视口已载入解析结构（{scene.structure.summary()}）"
        except (OSError, ValueError):
            note = "结构解析失败，仅显示原样渲染"

        image = QImage.fromData(outcome.png, "PNG")
        if not image.isNull():
            self.preview.show_pixmap(
                QPixmap.fromImage(image),
                f"{os.path.basename(job.pov_path)} · 原样渲染 · "
                f"{job.width}×{job.height} · {outcome.elapsed_ms:.0f} ms",
            )
        self._last_final = outcome
        self._raw_pov_path = job.pov_path
        self.save_png_action.setEnabled(True)
        self.statusBar().showMessage(
            f"已渲染 {os.path.basename(job.pov_path)}；{note}；"
            "改相机/背景/宽度后 Ctrl+Enter 重新渲染")

    # -------------------------------------------------------------- actions
    def export_pov(self) -> None:
        if self.structure is None:
            return
        if self.structure.source:
            default = os.path.splitext(self.structure.source)[0] + ".pov"
        elif self._default_dir():
            default = os.path.join(self._default_dir(), "scene.pov")
        else:
            default = "scene.pov"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 POV 场景", default, "POV-Ray 场景 (*.pov)")
        if not path:
            return
        try:
            sdl.write_pov(self.structure, self.params, path)
        except OSError as exc:
            QMessageBox.warning(self, "无法写入 POV 场景", str(exc))
            return
        self.statusBar().showMessage(f"已导出 {path}")

    def _save_png(self) -> None:
        if self._last_final is None:
            return
        if self.structure is not None and self.structure.source:
            default = os.path.splitext(self.structure.source)[0] + ".png"
        elif self._default_dir():
            default = os.path.join(self._default_dir(), "molecule.png")
        else:
            default = "molecule.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", default, "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            self._last_final.save(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法保存图片", str(exc))
            return
        message = f"已保存 {path}"
        if self._export_pov_with_image and self.structure is not None:
            pov_path = os.path.splitext(path)[0] + ".pov"
            try:
                sdl.write_pov(self.structure, self.params, pov_path)
                message += f" 与 {os.path.basename(pov_path)}"
            except OSError as exc:
                QMessageBox.warning(self, "无法写入 POV 场景", str(exc))
        self.statusBar().showMessage(message)

    def closeEvent(self, event) -> None:
        from PySide6.QtCore import QMetaObject

        self._debounce.stop()
        if not self._busy:
            try:
                QMetaObject.invokeMethod(self.worker, "shutdown",
                                         Qt.BlockingQueuedConnection)
            except RuntimeError:
                pass
        self._thread.quit()
        if not self._thread.wait(2500):
            self._thread.terminate()
            self._thread.wait(1500)
        super().closeEvent(event)


def _sample_path() -> str:
    root = os.path.dirname(HERE)
    for name in ("caffeine.sdf", "benzene.xyz"):
        path = os.path.join(root, "samples", name)
        if os.path.exists(path):
            return path
    return ""


def run(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    screenshot = None
    structure = None
    do_render = True
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--screenshot" and i + 1 < len(argv):
            screenshot = argv[i + 1]
            i += 2
        elif arg == "--no-render":
            do_render = False
            i += 1
        elif not arg.startswith("-") and structure is None:
            structure = arg
            i += 1
        else:
            i += 1

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("MolPOV")
    app.setStyle("Fusion")
    window = MainWindow()
    target = structure or _sample_path()
    if target:
        window.load(target)
    window.show()
    app.processEvents()

    if screenshot:
        if do_render:
            window._auto_preview = False
            loop = QEventLoop()
            window.worker.done.connect(lambda *_: loop.quit())
            QTimer.singleShot(500, lambda: window._submit(final=False))
            QTimer.singleShot(60000, loop.quit)
            loop.exec()
        app.processEvents()
        if window._last_outcome is not None and window._last_outcome.ok:
            with open(os.path.splitext(screenshot)[0] + "_pov.png", "wb") as handle:
                handle.write(window._last_outcome.png)
            print(f"raw pov: {os.path.splitext(screenshot)[0]}_pov.png "
                  f"canvas={window.preview.canvas.size().width()}x"
                  f"{window.preview.canvas.size().height()}")
        window.grab().save(screenshot)
        print(f"screenshot: {screenshot}")
        window.close()
        return 0

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
