import sys
import os
import json
import random
import subprocess
import threading
import ctypes
import ctypes.wintypes
import psutil
from datetime import datetime, timedelta, time
from PyQt6.QtWidgets import (QApplication, QLabel, QWidget, QMenu, QVBoxLayout,
                             QHBoxLayout, QDialog, QFormLayout, QLineEdit,
                             QPushButton, QSizePolicy, QTextEdit, QScrollArea,
                             QFrame, QTabWidget, QCalendarWidget, QGraphicsOpacityEffect,
                             QCheckBox)
from PyQt6.QtCore import (Qt, QSize, QTimer, pyqtSignal, QObject, QDate,
                          QPropertyAnimation, QEasingCurve, QPoint, QRectF, QPointF)
from PyQt6.QtGui import QMovie, QFont, QCursor, QTextCharFormat, QColor, QPainter, QPen, QPolygonF, QPixmap
from pynput import mouse, keyboard as pynput_keyboard

from work_log import (add_log, generate_weekly_report, understand_intent,
                      generate_daily_summary, replace_today_logs, load_logs,
                      get_today_activity_summaries)
from context_collector import ContextCollector
from coin_engine import CoinEngine, EQUIPMENT_CATALOG, TALENT_TREE
import data_collector

# ── 配置持久化 ────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_foreground_window_title() -> str:
    """获取当前前台窗口标题（Windows）"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def capture_fullscreen_base64() -> str:
    """全屏截图，返回 base64 编码的 PNG 字符串"""
    import base64
    import io
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        # 压缩到 1280 宽，减少传输体积和 token 消耗
        max_w = 1280
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        return ""


def analyze_screenshot(api_key: str, endpoint_id: str, image_b64: str, api_url: str = "") -> str:
    """
    把截图发给豆包视觉模型，返回一句话描述：用户在做什么。
    豆包视觉 API 和文本 API 同一个端点，messages 里加 image_url 字段。
    """
    import urllib.request
    import json
    if not image_b64:
        return ""
    payload = json.dumps({
        "model": endpoint_id,
        "max_tokens": 150,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}"
                    }
                },
                {
                    "type": "text",
                    "text": ("请观察这张屏幕截图，用一句话（不超过30字）描述用户正在做什么工作，"
                             "聚焦于主要的任务内容，例如「正在编写Java订单模块代码」「正在阅读需求文档」。"
                             "只输出这一句话，不加任何前缀或解释。")
                }
            ]
        }]
    }).encode("utf-8")
    from work_log import _DOUBAO_URL
    req = urllib.request.Request(
        api_url or _DOUBAO_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


# ── 跨线程信号桥 ──────────────────────────────────────────────
class Bridge(QObject):
    reply_ready = pyqtSignal(str, bool)       # (文本, 是否显示钉钉按钮)
    music_status = pyqtSignal(bool)           # 音乐进程检测结果
    daily_draft_ready = pyqtSignal(str)       # AI生成的日报草稿文本
    quick_record_trigger = pyqtSignal()       # 全局快捷键触发快速记录
    weekly_report_ready = pyqtSignal(str)     # AI生成的周报正文
    work_detected = pyqtSignal(str)           # ActivityMonitor检测到工作摘要


# ── 回复气泡 ──────────────────────────────────────────────────
class BubbleWidget(QWidget):
    open_dingtalk_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        self.text_label.setMaximumWidth(300)
        self.text_label.setStyleSheet("""
            QLabel {
                background: rgba(5, 8, 22, 240);
                color: #b8d8f8;
                border-radius: 12px;
                padding: 10px 16px;
                border: 1px solid rgba(0, 140, 255, 80);
                font-family: Microsoft YaHei, Arial;
                font-size: 12px;
                line-height: 1.6;
            }
        """)
        layout.addWidget(self.text_label)

        self.dingtalk_btn = QPushButton("📱 打开钉钉")
        self.dingtalk_btn.setVisible(False)
        self.dingtalk_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0a2060, stop:1 #0050a0);
                color: #90c8ff; border: 1px solid #1a4fa0;
                border-radius: 8px; padding: 5px 14px; font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #142878, stop:1 #0060b8);
                color: #c0e0ff;
            }
        """)
        self.dingtalk_btn.clicked.connect(self.open_dingtalk_clicked)
        layout.addWidget(self.dingtalk_btn)

        self._hide_timer = QTimer()
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # 思考动画
        self._thinking_timer = QTimer()
        self._thinking_timer.timeout.connect(self._tick_thinking)
        self._thinking_tick = 0
        self._show_anim = None

    def _tick_thinking(self):
        dots = ["●○○", "●●○", "●●●", "○●●", "○○●", "○○○"]
        self.text_label.setText(f"思考中  {dots[self._thinking_tick % len(dots)]}")
        self._thinking_tick += 1
        self.adjustSize()

    def show_thinking(self):
        """AI 处理中：显示滚动点动画，不自动隐藏"""
        self._thinking_timer.stop()
        self._thinking_tick = 0
        self._tick_thinking()
        self.dingtalk_btn.setVisible(False)
        self.adjustSize()
        self._hide_timer.stop()
        self._fade_in()

    def _fade_in(self):
        self.show()
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._show_anim = anim
        self._thinking_timer.start(350)

    def show_message(self, text: str, show_dingtalk: bool = False, auto_hide_ms: int = 6000):
        self._thinking_timer.stop()
        self.text_label.setText(text)
        self.dingtalk_btn.setVisible(show_dingtalk)
        self.adjustSize()
        self._fade_in()
        self._thinking_timer.stop()    # fade_in starts thinking timer, stop it again
        self._hide_timer.stop()
        if auto_hide_ms > 0:
            self._hide_timer.start(auto_hide_ms)


# ── 底部输入框 ─────────────────────────────────────────────────
class ChatInput(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.input = QLineEdit()
        self.input.setPlaceholderText("记录工作 / 生成周报 / 随便说点什么...")
        self.input.setFixedHeight(34)
        self.input.setStyleSheet("""
            QLineEdit {
                background: rgba(4, 6, 18, 225);
                color: #b8d4f8;
                border: 1px solid rgba(0, 80, 180, 80);
                border-radius: 10px;
                padding: 0 12px;
                font-family: Microsoft YaHei, Arial;
                font-size: 12px;
            }
            QLineEdit:focus { border: 1px solid rgba(0, 170, 255, 200); }
        """)
        self.input.returnPressed.connect(self._on_submit)

        send_btn = QPushButton("→")
        send_btn.setFixedSize(34, 34)
        send_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #0050b0, stop:1 #7010d0);
                color: #d0e8ff; border: none; border-radius: 10px; font-size: 16px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #0060c8, stop:1 #8820e8);
                color: white;
            }
        """)
        send_btn.clicked.connect(self._on_submit)

        layout.addWidget(self.input)
        layout.addWidget(send_btn)
        self.setFixedWidth(300)

    def _on_submit(self):
        text = self.input.text().strip()
        if text:
            self.submitted.emit(text)
            self.input.clear()


# ── 快速记录弹框（全局快捷键呼出）────────────────────────────
class QuickRecordPopup(QDialog):
    def __init__(self, on_submit_fn):
        super().__init__(None)
        self._on_submit_fn = on_submit_fn
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background: rgba(4, 6, 20, 248);
                border-radius: 14px;
                border: 1px solid rgba(0, 130, 255, 110);
            }
        """)
        inner = QVBoxLayout(container)
        inner.setContentsMargins(16, 14, 16, 14)
        inner.setSpacing(8)

        title = QLabel("⚡ 快速记录工作")
        title.setStyleSheet(
            "color:#00c8ff; font-family:Microsoft YaHei; font-size:13px; font-weight:bold;"
        )
        inner.addWidget(title)

        hint = QLabel("Enter 保存 · Esc 取消")
        hint.setStyleSheet(
            "color:#304870; font-size:10px; font-family:Microsoft YaHei;"
        )
        inner.addWidget(hint)

        self.input = QLineEdit()
        self.input.setPlaceholderText("今天做了什么...")
        self.input.setFixedHeight(36)
        self.input.setStyleSheet("""
            QLineEdit {
                background: rgba(3, 5, 16, 220);
                color: #c0d8ff;
                border: 1px solid rgba(0, 100, 200, 80);
                border-radius: 8px;
                padding: 0 12px;
                font-family: Microsoft YaHei, Arial;
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid rgba(0, 180, 255, 200); }
        """)
        self.input.returnPressed.connect(self._submit)
        inner.addWidget(self.input)

        outer.addWidget(container)
        self.setFixedWidth(320)

    def showEvent(self, event):
        super().showEvent(event)
        pos = QCursor.pos()
        screen = QApplication.primaryScreen().geometry()
        x = min(pos.x(), screen.width()  - self.width()  - 20)
        y = min(pos.y() + 20, screen.height() - self.height() - 20)
        self.move(max(0, x), max(0, y))
        self.adjustSize()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def _submit(self):
        text = self.input.text().strip()
        self.close()
        if text:
            self._on_submit_fn(text)


# ── 日报确认弹框 ──────────────────────────────────────────────
class DailyConfirmDialog(QDialog):
    """下班前弹出，展示AI草拟的日报，用户可编辑后确认保存"""

    def __init__(self, draft_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📋 今日工作日报确认")
        self.setWindowFlags(
            self.windowFlags() |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumWidth(480)
        self.setMinimumHeight(360)
        self.setStyleSheet("""
            QDialog { background: #060810; }
            QLabel { color: #b0ccee; font-family: Microsoft YaHei, Arial; }
            QTextEdit {
                background: #030510;
                color: #c8e0ff;
                border: 1px solid rgba(0, 100, 200, 80);
                border-radius: 8px; padding: 10px;
                font-family: Microsoft YaHei, Arial;
                font-size: 13px; line-height: 1.8;
            }
            QTextEdit:focus { border: 1px solid rgba(0, 180, 255, 180); }
            QPushButton {
                font-family: Microsoft YaHei, Arial;
                font-size: 13px; border-radius: 8px; padding: 8px 24px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # 标题行
        title_label = QLabel("📋 今日工作日报")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #00c8ff;")
        layout.addWidget(title_label)

        hint_label = QLabel("AI 已根据你今天的操作记录生成草稿，可直接编辑修改，确认后保存为今日日志。")
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("font-size: 11px; color: #7080a0; margin-bottom: 4px;")
        layout.addWidget(hint_label)

        # 可编辑文本区
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(draft_text)
        self.text_edit.setPlaceholderText("- 完成了某某功能开发\n- 修复了某某问题\n- 参加了某某会议")
        layout.addWidget(self.text_edit, 1)

        # 字数提示
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("font-size: 10px; color: #506080;")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.count_label)
        self.text_edit.textChanged.connect(self._update_count)
        self._update_count()

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        skip_btn = QPushButton("跳过（不保存）")
        skip_btn.setStyleSheet("""
            QPushButton { background: #0a0c1c; color: #506090; border: 1px solid #1a2850; }
            QPushButton:hover { background: #10142a; color: #7090b0; }
        """)
        skip_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton("✅ 确认保存")
        confirm_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #003880, stop:1 #0050a0);
                color: #a0d0ff; border: 1px solid #0050a0;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0048a0, stop:1 #0060be);
                color: white;
            }
        """)
        confirm_btn.clicked.connect(self.accept)
        confirm_btn.setDefault(True)

        btn_row.addWidget(skip_btn)
        btn_row.addStretch()
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _update_count(self):
        n = len(self.text_edit.toPlainText().strip().splitlines())
        self.count_label.setText(f"共 {n} 条")

    def get_entries(self) -> list:
        """把编辑框内容转成 work_log 格式的条目列表"""
        now_time = datetime.now().strftime("%H:%M")
        entries = []
        for line in self.text_edit.toPlainText().splitlines():
            line = line.strip().lstrip("-").strip()
            if line:
                entries.append({"time": now_time, "content": line})
        return entries


# ── 今日记录查看器 ────────────────────────────────────────────
_DARK_DIALOG_QSS = """
QDialog { background: #060810; }
QTabWidget::pane { border: 1px solid #0e2050; background: #060810; }
QTabBar::tab {
    background: #0a0e1e; color: #406090; padding: 6px 16px;
    border: 1px solid #0e2050; border-bottom: none;
    font-family: Microsoft YaHei, Arial; font-size: 12px;
}
QTabBar::tab:selected { background: #060810; color: #00c8ff; border-bottom: 1px solid #060810; }
QTextEdit {
    background: #030510; color: #b0ccec; border: 1px solid #0e2050;
    font-family: Microsoft YaHei, Consolas, monospace; font-size: 12px;
    line-height: 1.6;
}
QTextEdit:focus { border: 1px solid #0060cc; }
QPushButton {
    background: #0a0e20; color: #6090c0; border: 1px solid #1a3570;
    border-radius: 6px; padding: 5px 18px;
    font-family: Microsoft YaHei, Arial; font-size: 12px;
}
QPushButton:hover { background: #101828; color: #00c8ff; border: 1px solid #0060cc; }
QLabel { color: #8ab0d0; font-family: Microsoft YaHei, Arial; }
QScrollBar:vertical { background: #030510; width: 6px; border: none; }
QScrollBar::handle:vertical { background: #1a3060; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def _load_json_safe(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


class RecordsDialog(QDialog):
    """今日所有采集记录的查看弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("今日记录")
        self.setMinimumSize(560, 480)
        self.setStyleSheet(_DARK_DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        tabs = QTabWidget()
        tabs.addTab(self._make_work_log_tab(),    "📋 工作记录")
        tabs.addTab(self._make_activity_tab(),    "⏱ 活动时间线")
        tabs.addTab(self._make_app_usage_tab(),   "🖥 应用使用")
        tabs.addTab(self._make_input_tab(),       "⌨ 中文输入")
        layout.addWidget(tabs)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        close_btn.setFixedWidth(80)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)

    @staticmethod
    def _text_widget(text: str) -> QTextEdit:
        w = QTextEdit()
        w.setReadOnly(True)
        w.setPlainText(text)
        return w

    def _make_work_log_tab(self) -> QWidget:
        today = datetime.now().strftime("%Y-%m-%d")
        data  = _load_json_safe(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_log.json"), {}
        )
        entries = data.get(today, [])
        if entries:
            lines = [f"{e['time']}  {e['content']}" for e in entries]
            text  = f"【{today}】共 {len(entries)} 条\n\n" + "\n".join(lines)
        else:
            text = f"【{today}】今天还没有工作记录。\n\n通过输入框手动记录，或生成日报后确认保存。"
        return self._text_widget(text)

    def _make_activity_tab(self) -> QWidget:
        self._activity_log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "activity_log.json"
        )
        data    = _load_json_safe(self._activity_log_path, {})
        self._activity_date    = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        self._activity_entries = data.get("entries", [])

        container = QWidget()
        layout    = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        hint = QLabel("空白行可直接输入补充，编辑完点「保存」")
        hint.setStyleSheet("color:#6070a0;font-size:11px;padding:4px 8px;"
                           "font-family:Microsoft YaHei,Arial;")
        layout.addWidget(hint)

        self._activity_edit = QTextEdit()
        self._activity_edit.setStyleSheet(
            "background:#111122;color:#c8d0f0;border:none;"
            "font-family:Microsoft YaHei,Consolas,monospace;font-size:12px;"
        )
        if self._activity_entries:
            lines = [
                f"{e['time']}  {e['summary']}" if e.get("summary")
                else f"{e['time']}  "
                for e in self._activity_entries
            ]
            self._activity_edit.setPlainText("\n".join(lines))
        else:
            self._activity_edit.setPlainText(
                "暂无记录。\n程序启动约2分钟后开始记录，每2分钟更新一次。"
            )
        layout.addWidget(self._activity_edit)

        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(70)
        save_btn.clicked.connect(self._save_activity)
        self._activity_save_label = QLabel("")
        self._activity_save_label.setStyleSheet(
            "color:#60d080;font-size:11px;font-family:Microsoft YaHei,Arial;"
        )
        row = QHBoxLayout()
        row.addWidget(self._activity_save_label)
        row.addStretch()
        row.addWidget(save_btn)
        layout.addLayout(row)

        return container

    def _save_activity(self):
        """把编辑框内容解析回 activity_log.json"""
        lines = self._activity_edit.toPlainText().splitlines()
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 格式：HH:MM  内容（内容可为空）
            if len(line) >= 5 and line[2] == ":" and line[4:6] in ("  ", " "):
                t       = line[:5]
                summary = line[6:].strip()
                entries.append({"time": t, "summary": summary})
            else:
                # 无法解析时间戳，跳过
                pass
        try:
            with open(self._activity_log_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"date": self._activity_date, "entries": entries},
                    f, ensure_ascii=False, indent=2
                )
            self._activity_save_label.setText("✓ 已保存")
            QTimer.singleShot(2000, lambda: self._activity_save_label.setText(""))
        except Exception as e:
            self._activity_save_label.setText(f"保存失败：{e}")

    def _make_app_usage_tab(self) -> QWidget:
        today = datetime.now().strftime("%Y-%m-%d")
        data  = _load_json_safe(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw_behavior.json"), {}
        )
        day_data = data.get(today, {})
        apps     = day_data.get("apps", {})
        if apps:
            sorted_apps = sorted(apps.items(), key=lambda x: x[1], reverse=True)
            lines = []
            for app, secs in sorted_apps:
                h, m = divmod(int(secs) // 60, 60)
                lines.append(f"{app:<30} {h}h {m:02d}m")
            text = f"【{today}】\n\n" + "\n".join(lines)
        else:
            text = f"【{today}】暂无应用使用记录。"
        return self._text_widget(text)

    def _make_input_tab(self) -> QWidget:
        data     = _load_json_safe(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "input_log.json"), {}
        )
        segments = data.get("segments", [])
        if segments:
            text = f"今日中文输入片段（共 {len(segments)} 条）\n\n" + "\n".join(
                f"{i+1:>3}. {s}" for i, s in enumerate(segments)
            )
        else:
            text = "暂无中文输入记录。\n\n通过输入法输入的中文内容会自动采集。"
        return self._text_widget(text)


# ── 周报预览弹窗 ──────────────────────────────────────────────
class WeeklyReportDialog(QDialog):
    """展示AI生成的周报，可编辑后一键复制"""

    def __init__(self, report_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📋 本周周报")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(520, 420)
        self.setStyleSheet("""
            QDialog { background: #060810; }
            QLabel { color: #b0ccee; font-family: Microsoft YaHei, Arial; }
            QTextEdit {
                background: #030510; color: #c8e0ff;
                border: 1px solid rgba(0,100,200,80);
                border-radius: 8px; padding: 12px;
                font-family: Microsoft YaHei, Arial;
                font-size: 13px; line-height: 1.9;
            }
            QTextEdit:focus { border: 1px solid rgba(0,180,255,180); }
            QPushButton {
                font-family: Microsoft YaHei, Arial;
                font-size: 13px; border-radius: 8px; padding: 8px 24px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("📋 本周周报")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:#00c8ff;")
        layout.addWidget(title)

        hint = QLabel("可直接编辑修改，点「复制到剪贴板」后粘贴到钉钉。")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:11px;color:#406090;margin-bottom:4px;")
        layout.addWidget(hint)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(report_text)
        layout.addWidget(self.text_edit, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._copy_lbl = QLabel("")
        self._copy_lbl.setStyleSheet("font-size:11px;color:#00d080;")
        btn_row.addWidget(self._copy_lbl)
        btn_row.addStretch()

        copy_btn = QPushButton("📋 复制到剪贴板")
        copy_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #003880, stop:1 #0050a0);
                color: #a0d0ff; border: 1px solid #0050a0;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0048a0, stop:1 #0060be);
                color: white;
            }
        """)
        copy_btn.clicked.connect(self._copy)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("""
            QPushButton { background: #0a0c1c; color: #506090; border: 1px solid #1a2850; }
            QPushButton:hover { background: #10142a; color: #7090b0; }
        """)
        close_btn.clicked.connect(self.close)

        btn_row.addWidget(copy_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _copy(self):
        QApplication.clipboard().setText(self.text_edit.toPlainText())
        self._copy_lbl.setText("✓ 已复制！")
        QTimer.singleShot(2000, lambda: self._copy_lbl.setText(""))


# ── 工作检测确认弹窗 ─────────────────────────────────────────
class WorkDetectedPopup(QWidget):
    """
    ActivityMonitor 检测到工作摘要时弹出的非阻塞确认框。
    用户可编辑内容后点「确认记录」保存，或点「忽略」/等待20秒自动关闭。
    """
    _QSS = """
        WorkDetectedPopup {
            background: #060810;
            border: 1px solid #0060cc;
            border-radius: 10px;
        }
        QLabel#title_lbl {
            color: #00c8ff;
            font-size: 13px;
            font-weight: bold;
        }
        QLabel#hint_lbl {
            color: #4a6080;
            font-size: 11px;
        }
        QTextEdit {
            background: #030510;
            color: #b0ccec;
            border: 1px solid #0e2050;
            border-radius: 5px;
            font-size: 12px;
            padding: 4px;
        }
        QTextEdit:focus { border: 1px solid #0060cc; }
        QPushButton {
            background: #050c1a;
            color: #7eaacc;
            border: 1px solid #0e2050;
            border-radius: 5px;
            padding: 5px 14px;
            font-size: 12px;
        }
        QPushButton:hover { background: #101828; color: #00c8ff; border: 1px solid #0060cc; }
        QPushButton#confirm_btn { color: #00c8ff; border-color: #0060cc; }
        QPushButton#confirm_btn:hover { background: #0a2040; }
    """

    def __init__(self, summary: str, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(self._QSS)

        self._countdown = 20
        self._confirmed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title_lbl = QLabel("检测到工作记录 🔍")
        title_lbl.setObjectName("title_lbl")
        layout.addWidget(title_lbl)

        self._edit = QTextEdit()
        self._edit.setPlainText(summary)
        self._edit.setFixedHeight(68)
        layout.addWidget(self._edit)

        self._hint = QLabel(f"20 秒后自动忽略")
        self._hint.setObjectName("hint_lbl")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        confirm_btn = QPushButton("确认记录")
        confirm_btn.setObjectName("confirm_btn")
        ignore_btn = QPushButton("忽略")
        btn_row.addWidget(self._hint)
        btn_row.addStretch()
        btn_row.addWidget(confirm_btn)
        btn_row.addWidget(ignore_btn)
        layout.addLayout(btn_row)

        confirm_btn.clicked.connect(self._confirm)
        ignore_btn.clicked.connect(self.close)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self.setFixedWidth(320)
        self.adjustSize()
        self._position_near_corner()
        self._fade_in()

    def _position_near_corner(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20,
                  screen.bottom() - self.height() - 48)

    def _fade_in(self):
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim

    def _tick(self):
        self._countdown -= 1
        self._hint.setText(f"{self._countdown} 秒后自动忽略")
        if self._countdown <= 0:
            self._timer.stop()
            self.close()

    def _confirm(self):
        self._confirmed = True
        self._timer.stop()
        text = self._edit.toPlainText().strip()
        if text:
            from work_log import add_log
            add_log(text)
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        self.show()


# ── 工作日历 ──────────────────────────────────────────────────
class WorkCalendarDialog(QDialog):
    """查看并补录任意日期的工作记录"""

    _CAL_QSS = """
    QCalendarWidget QAbstractItemView {
        background: #111122; color: #c8d0f0;
        selection-background-color: #3a3a7a; selection-color: #ffffff;
        gridline-color: #252540;
    }
    QCalendarWidget QAbstractItemView:disabled { color: #383858; }
    QCalendarWidget QWidget#qt_calendar_navigationbar { background: #252540; }
    QCalendarWidget QToolButton {
        color: #c8d0f0; background: #252540; border: none;
        font-family: Microsoft YaHei, Arial; font-size: 13px;
        padding: 4px 8px;
    }
    QCalendarWidget QToolButton:hover { background: #3a3a6a; }
    QCalendarWidget QSpinBox {
        background: #252540; color: #c8d0f0; border: none;
        font-family: Microsoft YaHei, Arial;
    }
    QCalendarWidget QMenu { background: #252540; color: #c8d0f0; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("工作日历")
        self.setMinimumSize(760, 460)
        self.setStyleSheet(_DARK_DIALOG_QSS)

        self._log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_log.json")
        self._all_logs = _load_json_safe(self._log_file, {})
        self._selected_date = datetime.now().strftime("%Y-%m-%d")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(14)

        # ── 左侧日历 ──
        left = QVBoxLayout()
        left.setSpacing(4)
        self._cal = QCalendarWidget()
        self._cal.setStyleSheet(self._CAL_QSS)
        self._cal.setGridVisible(False)
        self._cal.setMaximumDate(QDate.currentDate())
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self._cal.selectionChanged.connect(self._on_date_changed)
        self._highlight_logged_dates()
        left.addWidget(self._cal)

        legend = QLabel("● 绿色 = 有记录")
        legend.setStyleSheet(
            "color:#60d080;font-size:11px;font-family:Microsoft YaHei,Arial;padding:4px 2px;")
        left.addWidget(legend)
        left.addStretch()
        outer.addLayout(left, 0)

        # ── 右侧编辑区 ──
        right = QVBoxLayout()
        right.setSpacing(6)

        self._date_label = QLabel()
        self._date_label.setStyleSheet(
            "color:#a0b8e0;font-size:13px;font-weight:bold;"
            "font-family:Microsoft YaHei,Arial;padding:2px 0;")
        right.addWidget(self._date_label)

        hint = QLabel("每行一条记录，格式：HH:MM  内容（时间可省略，保存时自动补全）")
        hint.setStyleSheet(
            "color:#5060a0;font-size:11px;font-family:Microsoft YaHei,Arial;")
        right.addWidget(hint)

        self._entry_edit = QTextEdit()
        self._entry_edit.setStyleSheet(
            "background:#111122;color:#c8d0f0;border:1px solid #333355;"
            "font-family:Microsoft YaHei,Consolas,monospace;font-size:12px;")
        right.addWidget(self._entry_edit)

        btn_row = QHBoxLayout()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:#60d080;font-size:11px;font-family:Microsoft YaHei,Arial;")
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(80)
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        right.addLayout(btn_row)

        outer.addLayout(right, 1)

        self._load_date(self._selected_date)

    def _highlight_logged_dates(self):
        fmt_has = QTextCharFormat()
        fmt_has.setBackground(QColor("#1a3a28"))
        fmt_has.setForeground(QColor("#60d080"))

        today_str = datetime.now().strftime("%Y-%m-%d")
        for date_str, entries in self._all_logs.items():
            if not entries or date_str == today_str:
                continue
            try:
                y, m, d = map(int, date_str.split("-"))
                self._cal.setDateTextFormat(QDate(y, m, d), fmt_has)
            except Exception:
                pass

        # 今天单独高亮：有记录→亮绿加粗，无记录→金色加粗
        fmt_today = QTextCharFormat()
        fmt_today.setFontWeight(700)
        if self._all_logs.get(today_str):
            fmt_today.setBackground(QColor("#1e4a30"))
            fmt_today.setForeground(QColor("#90f0a0"))
        else:
            fmt_today.setBackground(QColor("#3a2a10"))
            fmt_today.setForeground(QColor("#f0c060"))
        self._cal.setDateTextFormat(QDate.currentDate(), fmt_today)

    def _on_date_changed(self):
        qd = self._cal.selectedDate()
        self._selected_date = qd.toString("yyyy-MM-dd")
        self._load_date(self._selected_date)

    def _load_date(self, date_str: str):
        entries = self._all_logs.get(date_str, [])
        if entries:
            self._date_label.setText(f"{date_str}   共 {len(entries)} 条记录")
            lines = [f"{e['time']}  {e['content']}" for e in entries]
            self._entry_edit.setPlainText("\n".join(lines))
        else:
            self._date_label.setText(f"{date_str}   （暂无记录）")
            self._entry_edit.setPlainText("")

    def _save(self):
        import re
        date_str = self._selected_date
        is_today = date_str == datetime.now().strftime("%Y-%m-%d")
        default_time = datetime.now().strftime("%H:%M") if is_today else "09:00"

        entries = []
        for line in self._entry_edit.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d{2}:\d{2})\s{1,2}(.*)', line)
            if m:
                t, content = m.group(1), m.group(2).strip()
            else:
                t, content = default_time, line
            if content:
                entries.append({"time": t, "content": content})

        if entries:
            self._all_logs[date_str] = entries
        elif date_str in self._all_logs:
            del self._all_logs[date_str]

        try:
            with open(self._log_file, "w", encoding="utf-8") as f:
                json.dump(self._all_logs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._status_lbl.setText(f"保存失败：{e}")
            return

        self._highlight_logged_dates()
        self._load_date(date_str)
        self._status_lbl.setText("✓ 已保存")
        QTimer.singleShot(2000, lambda: self._status_lbl.setText(""))


# ── 金币商店 & 背包 ────────────────────────────────────────────
class CoinShopDialog(QDialog):
    _QSS = _DARK_DIALOG_QSS + """
        QLabel#balance_lbl { color: #ffdd57; font-size: 15px; font-weight: bold; }
        QLabel#section_lbl { color: #00c8ff; font-size: 12px; font-weight: bold; margin-top: 6px; }
        QLabel#item_name   { color: #c8ddf0; font-size: 12px; font-weight: bold; }
        QLabel#item_desc   { color: #5a7090; font-size: 11px; }
        QLabel#item_cost   { color: #ffaa22; font-size: 11px; }
    """

    def __init__(self, coin_engine: "CoinEngine", parent=None):
        super().__init__(parent)
        self._engine = coin_engine
        self.setWindowTitle("金币商店")
        self.setStyleSheet(self._QSS)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 14, 16, 14)

        # 余额行
        bal_row = QHBoxLayout()
        bal_lbl = QLabel("🪙 当前金币")
        bal_lbl.setStyleSheet("color:#7090b0;font-size:12px;")
        self._bal_val = QLabel()
        self._bal_val.setObjectName("balance_lbl")
        bal_row.addWidget(bal_lbl)
        bal_row.addStretch()
        bal_row.addWidget(self._bal_val)
        root.addLayout(bal_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#0e2050;")
        root.addWidget(sep)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabBar::tab { background:#030510; color:#405070; padding:6px 16px;
                           border:1px solid #0e2050; border-bottom:none; }
            QTabBar::tab:selected { background:#060810; color:#00c8ff;
                                    border-color:#0060cc; }
            QTabWidget::pane { border:1px solid #0e2050; background:#030510; }
        """)
        tabs.addTab(self._build_shop_tab(), "🏪 商店")
        tabs.addTab(self._build_bag_tab(),  "🎒 背包")
        root.addWidget(tabs)

        self._tabs = tabs
        self._status = QLabel("")
        self._status.setStyleSheet("color:#00c8ff;font-size:11px;")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self._refresh_balance()

    def _refresh_balance(self):
        self._bal_val.setText(f"{self._engine.get_all_time():.0f} 🪙")

    def _build_shop_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#030510;}")
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(6)
        inner_lay.setContentsMargins(6, 6, 6, 6)

        owned = self._engine.get_inventory() + self._engine.get_equipment()
        for eid, eq in EQUIPMENT_CATALOG.items():
            row = QHBoxLayout()
            icon_lbl = QLabel(f"{eq['icon']}  {eq['name']}")
            icon_lbl.setObjectName("item_name")
            desc_lbl = QLabel(eq["desc"])
            desc_lbl.setObjectName("item_desc")
            cost_lbl = QLabel(f"{eq['cost']} 🪙")
            cost_lbl.setObjectName("item_cost")

            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(icon_lbl)
            col.addWidget(desc_lbl)
            col.addWidget(cost_lbl)

            btn = QPushButton("已拥有" if eid in owned else "购买")
            btn.setFixedWidth(70)
            btn.setEnabled(eid not in owned)
            btn.clicked.connect(lambda _, e=eid: self._buy(e))

            row.addLayout(col)
            row.addStretch()
            row.addWidget(btn)

            frame = QFrame()
            frame.setStyleSheet(
                "QFrame{background:#060814;border:1px solid #0e2050;border-radius:6px;padding:4px;}")
            frame.setLayout(row)
            inner_lay.addWidget(frame)

        inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        return w

    def _build_bag_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#030510;}")
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(4)
        inner_lay.setContentsMargins(6, 6, 6, 6)

        equipped = self._engine.get_equipment()
        inv      = self._engine.get_inventory()

        eq_lbl = QLabel(f"已装备  ({len(equipped)}/{self._engine.MAX_EQUIP_SLOTS})")
        eq_lbl.setObjectName("section_lbl")
        inner_lay.addWidget(eq_lbl)
        for eid in equipped:
            eq = EQUIPMENT_CATALOG.get(eid, {})
            row = QHBoxLayout()
            name = QLabel(f"{eq.get('icon','')}  {eq.get('name','')}")
            name.setObjectName("item_name")
            row.addWidget(name)
            row.addStretch()
            btn = QPushButton("卸下")
            btn.setFixedWidth(60)
            btn.clicked.connect(lambda _, e=eid: self._unequip(e))
            row.addWidget(btn)
            frame = QFrame()
            frame.setStyleSheet(
                "QFrame{background:#081018;border:1px solid #006040;border-radius:6px;padding:3px;}")
            frame.setLayout(row)
            inner_lay.addWidget(frame)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#0e2050;margin:4px 0;")
        inner_lay.addWidget(sep)

        inv_lbl = QLabel("背包")
        inv_lbl.setObjectName("section_lbl")
        inner_lay.addWidget(inv_lbl)
        for eid in inv:
            eq = EQUIPMENT_CATALOG.get(eid, {})
            row = QHBoxLayout()
            name = QLabel(f"{eq.get('icon','')}  {eq.get('name','')}")
            name.setObjectName("item_name")
            row.addWidget(name)
            row.addStretch()
            btn = QPushButton("装备")
            btn.setFixedWidth(60)
            btn.clicked.connect(lambda _, e=eid: self._equip(e))
            row.addWidget(btn)
            frame = QFrame()
            frame.setStyleSheet(
                "QFrame{background:#060810;border:1px solid #0e2050;border-radius:6px;padding:3px;}")
            frame.setLayout(row)
            inner_lay.addWidget(frame)

        if not equipped and not inv:
            empty = QLabel("背包空空如也")
            empty.setStyleSheet("color:#304050;font-size:12px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner_lay.addWidget(empty)

        inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        return w

    def _buy(self, eid: str):
        ok, msg = self._engine.buy(eid)
        self._status.setText(("✅ " if ok else "❌ ") + msg)
        QTimer.singleShot(2500, lambda: self._status.setText(""))
        self._refresh_balance()
        # 重建标签页
        self._tabs.removeTab(0)
        self._tabs.removeTab(0)
        self._tabs.addTab(self._build_shop_tab(), "🏪 商店")
        self._tabs.addTab(self._build_bag_tab(),  "🎒 背包")
        self._tabs.setCurrentIndex(0)

    def _equip(self, eid: str):
        ok, msg = self._engine.equip(eid)
        self._status.setText(("✅ " if ok else "❌ ") + msg)
        QTimer.singleShot(2500, lambda: self._status.setText(""))
        self._rebuild_bag()

    def _unequip(self, eid: str):
        ok, msg = self._engine.unequip(eid)
        self._status.setText(("✅ " if ok else "❌ ") + msg)
        QTimer.singleShot(2500, lambda: self._status.setText(""))
        self._rebuild_bag()

    def _rebuild_bag(self):
        idx = self._tabs.currentIndex()
        self._tabs.removeTab(0)
        self._tabs.removeTab(0)
        self._tabs.addTab(self._build_shop_tab(), "🏪 商店")
        self._tabs.addTab(self._build_bag_tab(),  "🎒 背包")
        self._tabs.setCurrentIndex(idx)


# ── 天赋树 ─────────────────────────────────────────────────────
class TalentDialog(QDialog):
    _QSS = _DARK_DIALOG_QSS + """
        QLabel#pts_lbl { color: #ffdd57; font-size: 13px; font-weight: bold; }
        QFrame#card {
            background: #060814; border: 1px solid #0e2050;
            border-radius: 8px; padding: 6px;
        }
        QFrame#card_max { border-color: #00c8ff; }
        QLabel#t_icon   { font-size: 20px; }
        QLabel#t_name   { color: #c8ddf0; font-size: 12px; font-weight: bold; }
        QLabel#t_level  { color: #00c8ff; font-size: 11px; }
        QLabel#t_desc   { color: #5a7090; font-size: 11px; }
    """

    def __init__(self, coin_engine: "CoinEngine", parent=None):
        super().__init__(parent)
        self._engine = coin_engine
        self.setWindowTitle("天赋树")
        self.setStyleSheet(self._QSS)
        self.setMinimumWidth(460)

        self._root_lay = QVBoxLayout(self)
        self._root_lay.setSpacing(10)
        self._root_lay.setContentsMargins(16, 14, 16, 14)

        self._pts_lbl = QLabel()
        self._pts_lbl.setObjectName("pts_lbl")
        self._root_lay.addWidget(self._pts_lbl)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#0e2050;")
        self._root_lay.addWidget(sep)

        self._grid_widget = QWidget()
        self._root_lay.addWidget(self._grid_widget)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#00c8ff;font-size:11px;")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._root_lay.addWidget(self._status)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        self._root_lay.addWidget(close_btn)

        self._rebuild()

    def _rebuild(self):
        d = self._engine.get_display()
        pts = d["talent_pts"]
        level = d["level"]
        self._pts_lbl.setText(
            f"⭐ Lv.{level}   剩余天赋点: {pts}  （每升一级获得 1 点）")

        old = self._grid_widget
        new = QWidget()
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout(new)
        grid.setSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)

        talent_ids = list(TALENT_TREE.keys())
        talent_levels = self._engine.get_talent_levels()

        for i, tid in enumerate(talent_ids):
            info = TALENT_TREE[tid]
            cur  = talent_levels.get(tid, 0)
            maxl = info["max_level"]
            is_max = (cur >= maxl)

            card = QFrame()
            card.setObjectName("card_max" if is_max else "card")
            card.setObjectName("card")
            card_lay = QVBoxLayout(card)
            card_lay.setSpacing(3)
            card_lay.setContentsMargins(8, 8, 8, 8)

            icon_lbl = QLabel(info["icon"])
            icon_lbl.setObjectName("t_icon")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            name_lbl = QLabel(info["name"])
            name_lbl.setObjectName("t_name")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            lv_lbl = QLabel(f"Lv {cur} / {maxl}")
            lv_lbl.setObjectName("t_level")
            lv_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            desc_lbl = QLabel(info["desc"])
            desc_lbl.setObjectName("t_desc")
            desc_lbl.setWordWrap(True)
            desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            cost_txt = f"升级（消耗 {info['cost']} 点）" if not is_max else "✓ 已满级"
            upg_btn = QPushButton(cost_txt)
            upg_btn.setEnabled(not is_max and pts >= info["cost"])
            upg_btn.clicked.connect(lambda _, t=tid: self._spend(t))

            if is_max:
                card.setStyleSheet(
                    "QFrame{background:#081020;border:1px solid #0060cc;"
                    "border-radius:8px;padding:4px;}")

            card_lay.addWidget(icon_lbl)
            card_lay.addWidget(name_lbl)
            card_lay.addWidget(lv_lbl)
            card_lay.addWidget(desc_lbl)
            card_lay.addWidget(upg_btn)

            grid.addWidget(card, i // 2, i % 2)

        self._root_lay.replaceWidget(old, new)
        old.deleteLater()
        self._grid_widget = new

    def _spend(self, tid: str):
        ok, msg = self._engine.spend_talent_point(tid)
        self._status.setText(("✅ " if ok else "❌ ") + msg)
        QTimer.singleShot(2500, lambda: self._status.setText(""))
        self._rebuild()


# ── 设置对话框 ─────────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.setWindowTitle("设置")
        self.setStyleSheet("""
            QDialog { background: #060810; }
            QLabel { color: #7090b8; font-family: Microsoft YaHei, Arial; font-size: 12px; }
            QLineEdit {
                background: #030510; color: #b0ccf0;
                border: 1px solid #0e2050; border-radius: 6px;
                padding: 4px 8px; font-family: Microsoft YaHei, Arial; font-size: 12px;
                min-height: 22px;
            }
            QLineEdit:focus { border: 1px solid #0060cc; color: #c8e0ff; }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0a1030, stop:1 #0e1840);
                color: #6090c0; border: 1px solid #1a3570;
                border-radius: 6px; padding: 6px 20px;
                font-family: Microsoft YaHei, Arial; font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #102040, stop:1 #182850);
                color: #00c8ff; border: 1px solid #0060cc;
            }
        """)
        layout = QFormLayout(self)

        self.am_start_input       = QLineEdit(parent.am_start.strftime("%H:%M"))
        self.am_end_input         = QLineEdit(parent.am_end.strftime("%H:%M"))
        self.pm_start_input       = QLineEdit(parent.pm_start.strftime("%H:%M"))
        self.pm_end_input         = QLineEdit(parent.pm_end.strftime("%H:%M"))
        self.salary_input         = QLineEdit(str(parent.monthly_salary))
        self.idle_input           = QLineEdit(str(parent.idle_threshold_minutes))
        self.size_input           = QLineEdit(str(parent.pet_size))
        self.angry_thr_input      = QLineEdit(str(parent.activity_angry_threshold))
        self.angry_cd_input       = QLineEdit(str(parent.activity_cooldown))
        self.live_pet_checkbox     = QCheckBox("启用实时桌宠（关闭后播放 GIF）")
        self.live_pet_checkbox.setChecked(parent.live_pet_enabled)
        self.autonomous_idle_checkbox = QCheckBox("待机时自主散步并提示桌面文件名（仅读取名称）")
        self.autonomous_idle_checkbox.setChecked(parent.autonomous_idle_enabled)

        layout.addRow("上午上班时间 (HH:MM):", self.am_start_input)
        layout.addRow("上午下班时间 (HH:MM):", self.am_end_input)
        layout.addRow("下午上班时间 (HH:MM):", self.pm_start_input)
        layout.addRow("下午下班时间 (HH:MM):", self.pm_end_input)
        layout.addRow("月薪 (¥):", self.salary_input)
        layout.addRow("无操作判定时间 (分钟):", self.idle_input)
        layout.addRow("宠物大小 (px):", self.size_input)
        layout.addRow("连续操作触发愤怒 (秒):", self.angry_thr_input)
        layout.addRow("停止操作冷却时间 (秒):", self.angry_cd_input)
        layout.addRow("宠物表现:", self.live_pet_checkbox)
        layout.addRow("待机互动:", self.autonomous_idle_checkbox)

        sep = QLabel("── AI & 周报设置 ──")
        sep.setStyleSheet("color: #0099cc; margin-top: 8px; font-family: Microsoft YaHei;")
        layout.addRow(sep)

        rc = parent.report_config
        self.api_key_input = QLineEdit(rc.get("api_key", ""))
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("API Key（豆包/Gemini/OpenAI）")

        self.endpoint_input = QLineEdit(rc.get("endpoint_id", ""))
        self.endpoint_input.setPlaceholderText("模型名，如 gemini-2.0-flash-lite 或 ep-xxx")

        self.api_url_input = QLineEdit(rc.get("api_url", ""))
        self.api_url_input.setPlaceholderText(
            "留空=豆包  Gemini填: https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        )

        self.remind_time_input = QLineEdit(rc.get("remind_time", "11:00"))
        self.remind_day_input  = QLineEdit(str(rc.get("remind_day", 4)))

        self.dingtalk_path_input = QLineEdit(rc.get("dingtalk_path", ""))
        self.dingtalk_path_input.setPlaceholderText(r"C:\...\DingTalk.exe")

        layout.addRow("API Key:", self.api_key_input)
        layout.addRow("模型/接入点 ID:", self.endpoint_input)
        layout.addRow("API 接口地址:", self.api_url_input)
        layout.addRow("周报提醒时间 (HH:MM):", self.remind_time_input)
        layout.addRow("提醒星期几 (0=周一…4=周五):", self.remind_day_input)
        layout.addRow("钉钉 exe 路径:", self.dingtalk_path_input)

        btn = QPushButton("保存")
        btn.clicked.connect(self.save)
        layout.addRow(btn)

    def _parse_time(self, text):
        h, m = text.strip().split(":")
        return time(int(h), int(m))

    def save(self):
        self.parent.am_start                 = self._parse_time(self.am_start_input.text())
        self.parent.am_end                   = self._parse_time(self.am_end_input.text())
        self.parent.pm_start                 = self._parse_time(self.pm_start_input.text())
        self.parent.pm_end                   = self._parse_time(self.pm_end_input.text())
        self.parent.monthly_salary           = float(self.salary_input.text())
        self.parent.idle_threshold_minutes   = float(self.idle_input.text())
        self.parent.pet_size                 = int(self.size_input.text())
        self.parent.activity_angry_threshold = float(self.angry_thr_input.text())
        self.parent.activity_cooldown        = float(self.angry_cd_input.text())
        self.parent.live_pet_enabled         = self.live_pet_checkbox.isChecked()
        self.parent.autonomous_idle_enabled  = self.autonomous_idle_checkbox.isChecked()
        self.parent.live_pet.setVisible(self.parent.live_pet_enabled)
        self.parent.pet_label.setVisible(not self.parent.live_pet_enabled)
        if self.parent.live_pet_enabled:
            self.parent.live_pet.set_pet_size(self.parent.pet_size)
        self.parent.report_config = {
            "api_key":       self.api_key_input.text().strip(),
            "endpoint_id":   self.endpoint_input.text().strip(),
            "api_url":       self.api_url_input.text().strip(),
            "remind_time":   self.remind_time_input.text().strip(),
            "remind_day":    int(self.remind_day_input.text().strip() or "4"),
            "dingtalk_path": self.dingtalk_path_input.text().strip(),
        }
        self.parent.switch_gif(self.parent.current_gif, force=True)
        # 通知金币引擎更新日工资和工时
        _new_work_sec = (
            self.parent._time_diff_seconds(self.parent.am_start, self.parent.am_end) +
            self.parent._time_diff_seconds(self.parent.pm_start, self.parent.pm_end)
        )
        self.parent.coin_engine.update_salary(self.parent.monthly_salary, _new_work_sec)
        if self.parent.context_collector is not None:
            self.parent.context_collector.update_activity_config(
                self.parent.report_config["api_key"],
                self.parent.report_config["endpoint_id"],
                self.parent.report_config.get("api_url", ""),
            )
        save_config({
            "am_start":                 self.parent.am_start.strftime("%H:%M"),
            "am_end":                   self.parent.am_end.strftime("%H:%M"),
            "pm_start":                 self.parent.pm_start.strftime("%H:%M"),
            "pm_end":                   self.parent.pm_end.strftime("%H:%M"),
            "monthly_salary":           self.parent.monthly_salary,
            "idle_threshold_minutes":   self.parent.idle_threshold_minutes,
            "pet_size":                 self.parent.pet_size,
            "activity_cooldown":        self.parent.activity_cooldown,
            "activity_angry_threshold": self.parent.activity_angry_threshold,
            "live_pet":                self.parent.live_pet_enabled,
            "autonomous_idle":         self.parent.autonomous_idle_enabled,
            "report_config":            self.parent.report_config,
        })
        self.close()


# ── 实时桌宠 ────────────────────────────────────────────────
class LivePetWidget(QWidget):
    """不依赖 GIF 的轻量实时角色：呼吸、眨眼、视线和表情都由 QPainter 绘制。"""
    _MOODS = {
        "calm": (QColor("#55c8ff"), QColor("#1b5ca8")),
        "focused": (QColor("#45d5ef"), QColor("#144e96")),
        "tired": (QColor("#a8a8d4"), QColor("#52527e")),
        "intense": (QColor("#ff7890"), QColor("#9f2949")),
        "music": (QColor("#bb8cff"), QColor("#5e3798")),
        "off_work": (QColor("#ffbf55"), QColor("#b85a23")),
        "happy": (QColor("#77eea2"), QColor("#268856")),
        "thinking": (QColor("#45d5ef"), QColor("#144e96")),
        "wave": (QColor("#77eea2"), QColor("#268856")),
        "celebrate": (QColor("#77eea2"), QColor("#268856")),
        "stretch": (QColor("#a8a8d4"), QColor("#52527e")),
    }

    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self._mood, self._until, self._phase = "calm", 0, 0.0
        asset_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "live_pet")
        self._sprites = {
            name: QPixmap(os.path.join(asset_dir, f"{name}.png"))
            for name in ("calm", "happy", "tired", "wave", "thinking", "celebrate", "stretch",
                         "music_sway", "music_adjust")
        }
        self.set_pet_size(size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_pet_size(self, size: int):
        self.setFixedSize(max(72, size), max(72, size))

    def set_mood(self, mood: str, duration_ms: int = 0):
        # 短事件（如确认日报）优先于状态轮询，播完再自然回归当前状态。
        if self._until and datetime.now().timestamp() * 1000 < self._until and duration_ms == 0:
            return
        self._mood = mood if mood in self._MOODS else "calm"
        self._until = datetime.now().timestamp() * 1000 + duration_ms if duration_ms else 0
        self.update()

    def _tick(self):
        import math
        self._phase += 0.12
        if self._until and datetime.now().timestamp() * 1000 >= self._until:
            self._mood, self._until = "calm", 0
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, u = self.width(), self.height(), min(self.width(), self.height()) / 120.0
        breath, bob = math.sin(self._phase * .65) * 2.2 * u, math.sin(self._phase * .65) * 1.5 * u
        # 优先显示项目内的角色立绘；不同情绪切换表情，同时保持轻微呼吸感。
        if self._mood == "music":
            # 约每 4 秒在两种听歌姿势之间切换，配合呼吸浮动形成轻微律动。
            sprite_name = "music_sway" if int(self._phase / 15) % 2 == 0 else "music_adjust"
        else:
            sprite_name = self._mood if self._mood in self._sprites else (
                "happy" if self._mood == "off_work" else "calm"
            )
        sprite = self._sprites.get(sprite_name)
        if sprite and not sprite.isNull():
            scale = 0.96 + math.sin(self._phase * .65) * 0.012
            side = min(w, h) * scale
            target = QRectF((w - side) / 2, (h - side) / 2 + bob, side, side)
            p.drawPixmap(target, sprite, QRectF(sprite.rect()))
            return
        primary, dark = self._MOODS[self._mood]
        p.translate((w - 120 * u) / 2, (h - 120 * u) / 2 + bob)
        p.setPen(QPen(dark, 2.2 * u)); p.setBrush(primary)
        p.drawEllipse(QRectF(24*u, (36-breath)*u, 72*u, (67+breath)*u))
        p.drawPolygon(QPolygonF([QPointF(31*u, 50*u), QPointF(37*u, 19*u), QPointF(53*u, 43*u)]))
        p.drawPolygon(QPolygonF([QPointF(67*u, 43*u), QPointF(83*u, 19*u), QPointF(89*u, 50*u)]))
        local = self.mapFromGlobal(QCursor.pos())
        gaze = max(-1.0, min(1.0, (local.x() - w / 2) / max(w / 2, 1))) * 3.2 * u
        blink = (self._phase % 20) < .8 or self._mood == "tired"
        eye_pen = QPen(QColor("#0b1835"), 2.5*u)
        eye_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(eye_pen)
        if blink:
            p.drawLine(QPointF(42*u, 59*u), QPointF(52*u, 59*u)); p.drawLine(QPointF(68*u, 59*u), QPointF(78*u, 59*u))
        else:
            p.setBrush(QColor("#f7fcff")); p.drawEllipse(QRectF(40*u, 52*u, 15*u, 15*u)); p.drawEllipse(QRectF(65*u, 52*u, 15*u, 15*u))
            p.setBrush(QColor("#10214b")); p.drawEllipse(QRectF(45*u+gaze, 56*u, 6*u, 7*u)); p.drawEllipse(QRectF(70*u+gaze, 56*u, 6*u, 7*u))
        if self._mood == "intense":
            p.drawLine(QPointF(41*u, 48*u), QPointF(53*u, 51*u)); p.drawLine(QPointF(67*u, 51*u), QPointF(79*u, 48*u))
        mouth_pen = QPen(QColor("#0b1835"), 2.4*u)
        mouth_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(mouth_pen)
        if self._mood in ("happy", "off_work"):
            p.drawArc(QRectF(51*u, 64*u, 18*u, 14*u), 180*16, 180*16)
        elif self._mood == "tired":
            p.drawLine(QPointF(55*u, 73*u), QPointF(65*u, 73*u))
        else:
            p.drawArc(QRectF(52*u, 65*u, 16*u, 10*u), 195*16, 150*16)
        if self._mood == "happy":
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 135, 160, 160))
            p.drawEllipse(QRectF(31*u, 67*u, 10*u, 5*u)); p.drawEllipse(QRectF(79*u, 67*u, 10*u, 5*u))


# ── 主桌宠 ────────────────────────────────────────────────────
class DesktopPet(QWidget):
    def __init__(self):
        super().__init__()

        cfg = load_config()

        def _t(s, default):
            h, m = (s or default).split(":")
            return time(int(h), int(m))

        self.am_start = _t(cfg.get("am_start"), "9:00")
        self.am_end   = _t(cfg.get("am_end"),   "12:00")
        self.pm_start = _t(cfg.get("pm_start"), "13:00")
        self.pm_end   = _t(cfg.get("pm_end"),   "18:00")

        self.monthly_salary          = float(cfg.get("monthly_salary", 6000.0))
        self.idle_threshold_minutes  = float(cfg.get("idle_threshold_minutes", 5.0))
        self.pet_size                = int(cfg.get("pet_size", 120))
        self.live_pet_enabled        = bool(cfg.get("live_pet", True))
        self.autonomous_idle_enabled = bool(cfg.get("autonomous_idle", True))
        self._live_mood              = "calm"

        self.activity_start_time      = None
        self.activity_cooldown        = float(cfg.get("activity_cooldown", 2.0))
        self.activity_angry_threshold = float(cfg.get("activity_angry_threshold", 5.0))
        self.off_work_notified        = False

        self.report_config = cfg.get("report_config", {
            "api_key":       "",
            "endpoint_id":   "",
            "api_url":       "",
            "remind_time":   "11:00",
            "remind_day":    4,
            "dingtalk_path": "",
        })
        self.weekly_reminder_shown = False

        # 金币引擎
        _work_sec = (
            self._time_diff_seconds(self.am_start, self.am_end) +
            self._time_diff_seconds(self.pm_start, self.pm_end)
        )
        self.coin_engine = CoinEngine(self.monthly_salary, _work_sec)
        # 按当前时钟位置补齐今日基础金币（上班多久就补多少，重开也不会丢失）
        _now_t    = datetime.now().time()
        _elapsed  = (self._elapsed_in_period(_now_t, self.am_start, self.am_end) +
                     self._elapsed_in_period(_now_t, self.pm_start, self.pm_end))
        self.coin_engine.initialize_day(_elapsed)
        self._float_refs: list = []   # 浮动+1动画引用（防 GC）

        # 日报相关状态
        self._window_log = []           # 今天采集的窗口标题记录
        self._daily_confirm_shown = False  # 今天是否已弹过日报确认框
        self._off_work_dismissed = False  # 用户手动关掉了下班提醒
        self._proactive_flags: dict = {}  # 主动说话去重标记（含 _date 键）

        # 后台上下文/行为采集（音乐、中文输入、截图上下文 + 聊天/OCR/鼠标/浏览器历史）
        # 单个采集器出错不影响主程序，日报生成时会读取这里积累的上下文
        self._start_background_collectors()

        self.bridge = Bridge()
        self.bridge.reply_ready.connect(self._on_reply_ready)
        self.bridge.music_status.connect(self._on_music_status)
        self.bridge.daily_draft_ready.connect(self._on_daily_draft_ready)
        self.bridge.quick_record_trigger.connect(self._show_quick_record)
        self.bridge.weekly_report_ready.connect(self._on_weekly_report_ready)
        self.bridge.work_detected.connect(self._on_work_detected)
        self._last_work_popup_time: float = 0.0   # 弹窗限速（20分钟一次）
        if self.context_collector:
            self.context_collector.activity.set_on_summary_callback(
                lambda s: self.bridge.work_detected.emit(s)
            )
        self._music_playing = False

        self.last_activity_time = datetime.now()
        self._start_input_listeners()
        self._start_global_hotkeys()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 8, 8, 8)

        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet("""
            QLabel {
                background: rgba(4, 6, 18, 220);
                border-radius: 14px;
                padding: 10px 16px;
                border: 1px solid rgba(0, 100, 200, 70);
            }
        """)
        self.info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.info_label.linkActivated.connect(self._toggle_earn_visibility)
        main_layout.addWidget(self.info_label)

        self.pet_label = QLabel()
        self.pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_pet = LivePetWidget(self.pet_size)
        self.pet_label.setVisible(not self.live_pet_enabled)
        self.live_pet.setVisible(self.live_pet_enabled)
        main_layout.addWidget(self.pet_label)
        main_layout.addWidget(self.live_pet, alignment=Qt.AlignmentFlag.AlignCenter)

        self.adjustSize()
        self.show()

        self.bubble = BubbleWidget()
        self.bubble.open_dingtalk_clicked.connect(self._launch_dingtalk)

        self.chat_input = ChatInput()
        self.chat_input.submitted.connect(self._on_user_input)
        self.chat_input.show()

        self._update_child_positions()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_status)
        self.timer.start(2000)

        # 待机时的小范围自主活动；不会打开、修改或删除桌面文件。
        self._last_idle_wander = 0.0
        self._last_desktop_notice = 0.0
        self._idle_move_anim = None
        self.idle_pet_timer = QTimer()
        self.idle_pet_timer.timeout.connect(self._run_idle_pet_behavior)
        self.idle_pet_timer.start(15000)

        self.remind_timer = QTimer()
        self.remind_timer.timeout.connect(self._check_reminders)
        self.remind_timer.start(60000)

        # 每5分钟截图一次（上班时间段内才实际执行）
        self.window_capture_timer = QTimer()
        self.window_capture_timer.timeout.connect(self._async_capture_and_analyze)
        self.window_capture_timer.start(5 * 60 * 1000)

        # 音乐检测每10秒一次
        self.music_check_timer = QTimer()
        self.music_check_timer.timeout.connect(self._async_check_music)
        self.music_check_timer.start(10000)
        self._async_check_music()

        # 金币引擎：每秒 tick + 每30秒写盘
        self.coin_timer = QTimer()
        self.coin_timer.timeout.connect(self._coin_tick)
        self.coin_timer.start(1000)
        self.coin_save_timer = QTimer()
        self.coin_save_timer.timeout.connect(self.coin_engine.save_periodic)
        self.coin_save_timer.start(30000)

        self.current_gif = None
        self._last_animation_by_state: dict[str, str] = {}
        self.switch_gif("manei.gif")

    # ── 后台采集器启动 ────────────────────────────────────────
    def _start_background_collectors(self):
        """
        启动 context_collector（音乐/中文输入/截图上下文）和
        data_collector（聊天内容/OCR/鼠标活跃度/浏览器历史）。
        任一采集器初始化失败都不应该影响桌宠主程序启动。
        """
        api_key     = self.report_config.get("api_key", "")
        endpoint_id = self.report_config.get("endpoint_id", "")
        api_url     = self.report_config.get("api_url", "")

        try:
            self.context_collector = ContextCollector()
            self.context_collector.start_all(
                screenshot_enabled=bool(api_key and endpoint_id),
                api_key=api_key,
                endpoint_id=endpoint_id,
                api_url=api_url,
            )
        except Exception:
            self.context_collector = None

        try:
            data_collector.start_all(api_key=api_key, endpoint_id=endpoint_id)
        except Exception:
            pass

    # ── 截图 + 视觉分析 ───────────────────────────────────────
    def _async_capture_and_analyze(self):
        """
        检查当前是否在上班时间段，是则后台截图并调视觉API分析，
        结果写入 _window_log（和窗口标题共用同一个列表，日报时统一使用）。
        """
        now_t = datetime.now().time()
        in_work = (self.am_start <= now_t <= self.am_end or
                   self.pm_start <= now_t < self.pm_end)
        if not in_work:
            return

        api_key     = self.report_config.get("api_key", "")
        endpoint_id = self.report_config.get("endpoint_id", "")
        api_url     = self.report_config.get("api_url", "")

        def run():
            ts = datetime.now().strftime("%H:%M")
            # 先记一条窗口标题作为保底（视觉API失败时也有记录）
            title = get_foreground_window_title()
            skip_kw = {"WorkingPet", "桌宠", "Python", ""}
            if title and not any(k in title for k in skip_kw):
                self._window_log.append({"time": ts, "title": title})

            # 如果有视觉API配置，追加截图分析结果
            if api_key and endpoint_id:
                image_b64 = capture_fullscreen_base64()
                if image_b64:
                    desc = analyze_screenshot(api_key, endpoint_id, image_b64, api_url)
                    if desc:
                        self._window_log.append({"time": ts, "screenshot": desc})

        threading.Thread(target=run, daemon=True).start()

    # ── 日报确认触发 ──────────────────────────────────────────
    def _trigger_daily_confirm(self):
        """在下班前10分钟触发，后台生成AI草稿，准备好后弹框"""
        api_key     = self.report_config.get("api_key", "")
        endpoint_id = self.report_config.get("endpoint_id", "")
        api_url     = self.report_config.get("api_url", "")

        if not api_key or not endpoint_id:
            # 没有AI配置，直接弹空框让用户手填
            self.bridge.daily_draft_ready.emit("（未配置AI，请手动填写今日工作内容）\n- ")
            return

        self.bubble.show_thinking()
        self.live_pet.set_mood("thinking", 30000)
        QTimer.singleShot(4000, lambda: self.bubble._thinking_timer.stop()
                          if not self.bubble.isVisible() else None)

        today = datetime.now().strftime("%Y-%m-%d")
        manual_entries = load_logs().get(today, [])
        activity_entries = get_today_activity_summaries()
        context_text = ""
        if self.context_collector is not None:
            try:
                context_text = self.context_collector.get_context_snapshot()
            except Exception:
                context_text = ""

        def run():
            try:
                draft = generate_daily_summary(
                    api_key, endpoint_id,
                    self._window_log,
                    manual_entries,
                    context_text,
                    activity_entries,
                    api_url
                )
                self.bridge.daily_draft_ready.emit(draft)
            except Exception as e:
                self.bridge.daily_draft_ready.emit(f"（AI生成失败：{e}）\n- ")

        threading.Thread(target=run, daemon=True).start()

    def _on_weekly_report_ready(self, report_text: str):
        """周报生成完毕，在主线程弹窗展示并提供复制按钮"""
        self._show_bubble("📋 周报已生成，请查看弹窗！", False)
        dlg = WeeklyReportDialog(report_text, parent=None)
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()

    def _on_work_detected(self, summary: str):
        """ActivityMonitor 检测到工作内容，弹出非阻塞确认框（20分钟限速）"""
        import time as _time
        now = _time.time()
        if now - self._last_work_popup_time < 20 * 60:
            return
        self._last_work_popup_time = now
        popup = WorkDetectedPopup(summary, parent=None)
        popup.show()

    def _on_daily_draft_ready(self, draft_text: str):
        """AI草稿生成完毕，弹出确认框"""
        dialog = DailyConfirmDialog(draft_text, parent=None)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            entries = dialog.get_entries()
            if entries:
                replace_today_logs(entries)
                self.live_pet.set_mood("celebrate", 8000)
                self._show_bubble(f"✅ 今日日报已保存！共 {len(entries)} 条记录", False)
            else:
                self._show_bubble("⚠️ 没有内容，日报未保存", False)
        else:
            self._show_bubble("跳过了日报确认，可以手动补录哦~", False)

    # ── 音乐检测 ──────────────────────────────────────────────
    def _on_music_status(self, playing: bool):
        self._music_playing = playing

    def _async_check_music(self):
        def check():
            music_apps = {"QQMusic.exe", "cloudmusic.exe"}
            try:
                playing = any(p.name() in music_apps for p in psutil.process_iter(['name']))
            except Exception:
                playing = False
            self.bridge.music_status.emit(playing)
        threading.Thread(target=check, daemon=True).start()

    # ── 子窗口位置跟随 ────────────────────────────────────────
    def _update_child_positions(self):
        g = self.frameGeometry()
        self.chat_input.move(
            g.left() + (g.width() - self.chat_input.width()) // 2,
            g.bottom() + 6
        )
        if self.bubble.isVisible():
            self.bubble.move(
                g.left() + (g.width() - self.bubble.width()) // 2,
                self.chat_input.y() + self.chat_input.height() + 6
            )

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, 'chat_input'):
            self._update_child_positions()

    def _run_idle_pet_behavior(self):
        """待机时偶尔散步，并以只读方式提示一个桌面文件名。"""
        import time as _time
        if (not self.autonomous_idle_enabled or self.bubble.isVisible() or
                not self._is_work_hour()):
            return
        idle_seconds = (datetime.now() - self.last_activity_time).total_seconds()
        if idle_seconds < self.idle_threshold_minutes * 60:
            return
        now = _time.time()
        if now - self._last_idle_wander >= 45:
            screen = QApplication.primaryScreen().availableGeometry()
            target_x = self.x() + random.randint(-140, 140)
            target_y = self.y() + random.randint(-80, 80)
            target_x = max(screen.left(), min(target_x, screen.right() - self.width()))
            target_y = max(screen.top(), min(target_y, screen.bottom() - self.height()))
            self._idle_move_anim = QPropertyAnimation(self, b"pos")
            self._idle_move_anim.setDuration(1400)
            self._idle_move_anim.setStartValue(self.pos())
            self._idle_move_anim.setEndValue(QPoint(target_x, target_y))
            self._idle_move_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
            self._idle_move_anim.start()
            self._last_idle_wander = now

        # 每 5 分钟最多一次：仅读取文件名，不访问文件内容、更不执行文件。
        if now - self._last_desktop_notice >= 300:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            try:
                names = [name for name in os.listdir(desktop)
                         if not name.startswith(".") and name not in ("desktop.ini",)]
            except OSError:
                names = []
            if names:
                self._show_bubble(f"👀 我发现桌面上的「{random.choice(names)}」", False, auto_hide_ms=5000)
                self.live_pet.set_mood("thinking", 5000)
                self._last_desktop_notice = now

    # ── 用户输入处理 ──────────────────────────────────────────
    def _on_user_input(self, text: str):
        api_key     = self.report_config.get("api_key", "")
        endpoint_id = self.report_config.get("endpoint_id", "")
        api_url     = self.report_config.get("api_url", "")

        if not api_key or not endpoint_id:
            count = add_log(text)
            self._show_bubble(f"✅ 已记录！今天共 {count} 条记录", False)
            return

        self.bubble.show_thinking()
        self.live_pet.set_mood("thinking", 30000)

        def run():
            try:
                result = understand_intent(api_key, endpoint_id, text, api_url)
                intent = result.get("intent", "chat")

                if intent == "record":
                    count = add_log(result.get("content", text))
                    self.bridge.reply_ready.emit(f"✅ 已记录！今天共 {count} 条记录", False)

                elif intent == "generate_report":
                    report = generate_weekly_report(api_key, endpoint_id, api_url)
                    self.bridge.weekly_report_ready.emit(report)

                elif intent == "open_dingtalk":
                    self.bridge.reply_ready.emit("好的，正在打开钉钉...", False)
                    self._launch_dingtalk()

                else:
                    self.bridge.reply_ready.emit(result.get("content", "嗯嗯～"), False)

            except Exception as e:
                self.bridge.reply_ready.emit(f"出错了：{e}", False)

        threading.Thread(target=run, daemon=True).start()

    def _on_reply_ready(self, text: str, show_dingtalk: bool):
        self.live_pet.set_mood("wave", 4500)
        self._show_bubble(text, show_dingtalk)
        self._bounce_pet()

    def _bounce_pet(self):
        pos = self.pos()
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(400)
        anim.setKeyValueAt(0.0, pos)
        anim.setKeyValueAt(0.25, pos + QPoint(0, -10))
        anim.setKeyValueAt(0.6,  pos + QPoint(0, -4))
        anim.setKeyValueAt(1.0,  pos)
        anim.setEasingCurve(QEasingCurve.Type.OutBounce)
        anim.finished.connect(self._update_child_positions)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._bounce_anim = anim

    def _trigger_weekly_report(self):
        """手动触发周报生成，生成后复制到剪贴板并弹气泡"""
        api_key     = self.report_config.get("api_key", "")
        endpoint_id = self.report_config.get("endpoint_id", "")
        api_url     = self.report_config.get("api_url", "")
        if not api_key or not endpoint_id:
            self._show_bubble("⚠️ 请先在设置里配置 API Key", False)
            return
        self.bubble.show_thinking()
        self.live_pet.set_mood("thinking", 30000)
        def run():
            try:
                report = generate_weekly_report(api_key, endpoint_id, api_url)
                self.bridge.weekly_report_ready.emit(report)
            except Exception as e:
                self.bridge.reply_ready.emit(f"周报生成失败：{e}", False)
        threading.Thread(target=run, daemon=True).start()

    def _show_bubble(self, text: str, show_dingtalk: bool, auto_hide_ms: int = 6000):
        self.bubble.show_message(text, show_dingtalk, auto_hide_ms)
        self._update_child_positions()

    def _launch_dingtalk(self):
        path = self.report_config.get("dingtalk_path", "")
        if path and os.path.exists(path):
            subprocess.Popen([path])
            self._show_bubble("✅ 已打开钉钉！", False)
        else:
            try:
                subprocess.Popen(["DingTalk.exe"])
                self._show_bubble("✅ 已打开钉钉！", False)
            except Exception:
                self._show_bubble("❌ 找不到钉钉，请在设置里配置路径", False)

    # ── 定时检查：日报 + 周报 + 主动说话 ────────────────────────
    def _check_reminders(self):
        now = datetime.now()
        now_t = now.time()

        # 日报确认：下班前10分钟触发，每天只弹一次
        daily_trigger = timedelta(
            hours=self.pm_end.hour, minutes=self.pm_end.minute
        ) - timedelta(minutes=10)
        trigger_h = int(daily_trigger.seconds // 3600)
        trigger_m = int((daily_trigger.seconds % 3600) // 60)

        if (now_t.hour == trigger_h and now_t.minute == trigger_m
                and not self._daily_confirm_shown):
            self._daily_confirm_shown = True
            self._trigger_daily_confirm()

        # 次日重置
        if now_t < time(trigger_h, trigger_m):
            self._daily_confirm_shown = False

        # 周报提醒
        cfg = self.report_config
        if now.weekday() == cfg.get("remind_day", 4):
            h, m = map(int, cfg.get("remind_time", "11:00").split(":"))
            if now.hour == h and now.minute == m and not self.weekly_reminder_shown:
                self.weekly_reminder_shown = True
                self._show_bubble("📋 周报时间到！\n要生成本周周报吗？", False, auto_hide_ms=0)
                self.chat_input.input.setPlaceholderText('回复"生成周报"来生成周报...')
        else:
            self.weekly_reminder_shown = False

        self._check_proactive_talk()

    # ── 状态逻辑 ──────────────────────────────────────────────
    def _start_input_listeners(self):
        def on_activity(*args):
            now = datetime.now()
            if self.activity_start_time is None or \
               (now - self.last_activity_time).total_seconds() > self.activity_cooldown:
                self.activity_start_time = now
            self.last_activity_time = now

        ml = mouse.Listener(on_move=on_activity, on_click=on_activity, on_scroll=on_activity)
        ml.daemon = True
        ml.start()
        kl = pynput_keyboard.Listener(on_press=on_activity)
        kl.daemon = True
        kl.start()

    def _start_global_hotkeys(self):
        """注册 Ctrl+Alt+W 全局热键，触发快速记录弹框"""
        try:
            hotkeys = pynput_keyboard.GlobalHotKeys({
                '<ctrl>+<alt>+w': lambda: self.bridge.quick_record_trigger.emit()
            })
            hotkeys.daemon = True
            hotkeys.start()
        except Exception:
            pass

    def _show_quick_record(self):
        popup = QuickRecordPopup(self._on_user_input)
        popup.exec()

    def _check_proactive_talk(self):
        """每分钟检查是否需要主动说话，同类型提醒每天只弹一次"""
        if self.bubble.isVisible():
            return

        now   = datetime.now()
        today = now.strftime("%Y-%m-%d")
        now_t = now.time()

        # 每天重置标记
        if self._proactive_flags.get("_date") != today:
            self._proactive_flags = {"_date": today}

        # 只在上班时间段内触发
        in_work = (self.am_start <= now_t <= self.am_end or
                   self.pm_start <= now_t < self.pm_end)
        if not in_work:
            return

        # 1. 连续工作 2 小时
        if self.activity_start_time is not None:
            elapsed = (now - self.activity_start_time).total_seconds()
            if elapsed >= 7200:
                session_key = f"work_2h_{self.activity_start_time.strftime('%H%M')}"
                if session_key not in self._proactive_flags:
                    self._proactive_flags[session_key] = True
                    self._show_bubble(
                        "⏰ 你已连续工作 2 小时了，起来活动一下吧～", False, auto_hide_ms=10000
                    )
                    self.live_pet.set_mood("stretch", 9000)
                    return

        # 2. 下午了还没有工作记录
        if (now_t >= self.pm_start and
                "no_record" not in self._proactive_flags):
            today_logs = load_logs().get(today, [])
            if not today_logs:
                self._proactive_flags["no_record"] = True
                self._show_bubble(
                    "📝 下午了还没有工作记录，记得补一下哦～", False, auto_hide_ms=10000
                )
                return

        # 3. 距离下班 15 分钟
        remaining = self._remaining_minutes(now_t)
        if (0 < remaining <= 15 and
                "end_soon" not in self._proactive_flags):
            self._proactive_flags["end_soon"] = True
            self._show_bubble(
                f"⏳ 还有 {int(remaining)} 分钟下班，快收尾了！", False, auto_hide_ms=10000
            )
            return

    def _get_state(self, now):
        now_t = now.time()

        if now_t >= self.pm_end and not self._off_work_dismissed:
            if not self.off_work_notified:
                self.off_work_notified = True
                self._enter_off_work_mode()
            self._live_mood = "off_work"
            return self._pick_animation("off_work", "xiaban.gif"), "🚨 下班了！快跑！"

        if self.am_start <= now_t <= self.am_end or self.pm_start <= now_t < self.pm_end:
            if self.off_work_notified:
                self._exit_off_work_mode()
            self.off_work_notified = False

        idle_seconds = (now - self.last_activity_time).total_seconds()

        if idle_seconds > self.activity_angry_threshold:
            self.activity_start_time = None

        is_active = idle_seconds <= self.activity_cooldown
        if is_active and self.activity_start_time is not None:
            if (now - self.activity_start_time).total_seconds() >= self.activity_angry_threshold:
                self._live_mood = "intense"
                return self._pick_animation("intense", "angry.gif"), "😤 别烦我，我在赶！"

        if idle_seconds >= self.idle_threshold_minutes * 60:
            self._live_mood = "tired"
            return self._pick_animation("idle", "SLEEPY.gif"), "😴 摸鱼中..."

        if self._music_playing:
            self._live_mood = "music"
            return self._pick_animation("music", "MUSIC.gif"), "🎧 听歌搬砖中..."

        self._live_mood = "focused"
        return self._pick_animation("focus", "manei.gif"), "💻 正常工作中..."

    def _pick_animation(self, state: str, fallback: str) -> str:
        """按语义状态从 assets/animations/<state>/ 随机选 GIF，缺资源时安全回退。"""
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "animations", state)
        try:
            variants = [os.path.join(root, name) for name in os.listdir(root)
                        if name.lower().endswith(".gif")]
        except OSError:
            variants = []
        if not variants:
            return fallback
        last = self._last_animation_by_state.get(state)
        choices = [path for path in variants if path != last] or variants
        selected = random.choice(choices)
        self._last_animation_by_state[state] = selected
        return selected

    def _enter_off_work_mode(self):
        self._normal_pet_size = self.pet_size
        self._normal_pos = self.pos()           # 记住下班前的位置
        self.pet_size = self._normal_pet_size * 3
        self.switch_gif("xiaban.gif", force=True)
        self.adjustSize()
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2
        )

    def _exit_off_work_mode(self):
        if hasattr(self, '_normal_pet_size'):
            self.pet_size = self._normal_pet_size
        self._off_work_dismissed = False

    def _dismiss_off_work_mode(self):
        self._off_work_dismissed = True
        self._exit_off_work_mode()
        self.switch_gif("manei.gif", force=True)
        self.adjustSize()
        if hasattr(self, '_normal_pos'):        # 回到下班前的位置
            self.move(self._normal_pos)
        self._update_child_positions()
        self.update_status()

    def _toggle_earn_visibility(self, url=None):
        if url == "dismiss_offwork":
            self._dismiss_off_work_mode()
        elif url == "open_shop":
            CoinShopDialog(self.coin_engine, self).exec()

    def _equip_slots_html(self) -> str:
        """生成装备格 HTML：已装备道具显示图标，空格占位，点击打开背包"""
        equipped = self.coin_engine.get_equipment()
        parts = []
        for i in range(self.coin_engine.MAX_EQUIP_SLOTS):
            if i < len(equipped):
                eq   = EQUIPMENT_CATALOG.get(equipped[i], {})
                icon = eq.get("icon", "?")
                name = eq.get("name", "")
                parts.append(
                    f"<span style='font-size:15px;border:1px solid #0e3060;"
                    f"border-radius:4px;padding:1px 3px;"
                    f"background:#060c18;' title='{name}'>{icon}</span>")
            else:
                parts.append(
                    "<span style='font-size:15px;border:1px solid #0e2040;"
                    "border-radius:4px;padding:1px 4px;"
                    "background:#030810;color:#1a2840;'>⬜</span>")
        slots = "&nbsp;".join(parts)
        return (f"<a href='open_shop' style='text-decoration:none;'>"
                f"<span style='font-size:9px;color:#6888a8;'>装备栏</span>"
                f"&nbsp;{slots}</a>")

    def _calc_earned(self, now):
        daily_work_seconds = (
            self._time_diff_seconds(self.am_start, self.am_end) +
            self._time_diff_seconds(self.pm_start, self.pm_end)
        )
        if daily_work_seconds <= 0:
            return 0.0
        sps = (self.monthly_salary / 21.75) / daily_work_seconds
        now_t = now.time()
        secs = (self._elapsed_in_period(now_t, self.am_start, self.am_end) +
                self._elapsed_in_period(now_t, self.pm_start, self.pm_end))
        return secs * sps

    def _elapsed_in_period(self, now_t, start, end):
        if now_t <= start:  return 0
        elif now_t >= end:  return self._time_diff_seconds(start, end)
        else:               return self._time_diff_seconds(start, now_t)

    def _time_diff_seconds(self, t1, t2):
        d1 = timedelta(hours=t1.hour, minutes=t1.minute)
        d2 = timedelta(hours=t2.hour, minutes=t2.minute)
        return max(0, (d2 - d1).total_seconds())

    def _remaining_minutes(self, now_t):
        end = timedelta(hours=self.pm_end.hour, minutes=self.pm_end.minute)
        cur = timedelta(hours=now_t.hour, minutes=now_t.minute, seconds=now_t.second)
        return (end - cur).total_seconds() / 60

    # ── 金币引擎 ─────────────────────────────────────────────
    def _is_work_hour(self) -> bool:
        t = datetime.now().time()
        return (self.am_start <= t <= self.am_end or
                self.pm_start <= t < self.pm_end)

    def _coin_tick(self):
        is_wh = self._is_work_hour()
        is_working = bool(
            self.context_collector and
            self.context_collector.activity.get_summary()
        )
        idle_secs = (datetime.now() - self.last_activity_time).total_seconds()
        is_idle   = idle_secs > self.idle_threshold_minutes * 60
        self.coin_engine.tick(is_wh, is_working, is_idle)
        # +1 动画：上班时每秒都弹（视觉效果，与实际小数金币积累解耦）
        if is_wh:
            self._show_coin_float()

    def _show_coin_float(self):
        lbl = QLabel("+1 🪙")
        lbl.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lbl.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lbl.setStyleSheet(
            "color:#ffdd57;font-size:13px;font-weight:bold;background:transparent;")
        lbl.adjustSize()

        pet_geo = self.pet_label.geometry()
        origin  = self.mapToGlobal(pet_geo.topLeft())
        x = origin.x() + pet_geo.width() // 2 - lbl.width() // 2
        y = origin.y() + 10
        lbl.move(x, y)
        lbl.show()

        effect = QGraphicsOpacityEffect(lbl)
        lbl.setGraphicsEffect(effect)

        pos_anim = QPropertyAnimation(lbl, b"pos")
        pos_anim.setDuration(1100)
        pos_anim.setStartValue(QPoint(x, y))
        pos_anim.setEndValue(QPoint(x, y - 50))
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        op_anim = QPropertyAnimation(effect, b"opacity")
        op_anim.setDuration(1100)
        op_anim.setStartValue(1.0)
        op_anim.setEndValue(0.0)
        op_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        ref = [lbl, effect, pos_anim, op_anim]
        self._float_refs.append(ref)

        def _done():
            lbl.close()
            try:
                self._float_refs.remove(ref)
            except ValueError:
                pass

        op_anim.finished.connect(_done)
        pos_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        op_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    @staticmethod
    def _xp_bar_str(pct: int) -> str:
        filled = max(0, min(10, pct // 10))
        return (f"<span style='color:#0080cc;'>{'▰' * filled}</span>"
                f"<span style='color:#1a2840;'>{'▱' * (10 - filled)}</span>")

    # ── UI 刷新 ───────────────────────────────────────────────
    def update_status(self):
        now   = datetime.now()
        now_t = now.time()
        gif, status = self._get_state(now)
        self.switch_gif(gif)

        # 正常工作状态下，用实时活动摘要替换静态"正常工作中"文字
        if status == "💻 正常工作中..." and self.context_collector is not None:
            activity = self.context_collector.activity.get_summary()
            if activity:
                status = f"📝 {activity}"

        remaining_total_min = max(0, int(self._remaining_minutes(now_t)))
        if remaining_total_min >= 60:
            remaining_str = f"{remaining_total_min // 60}h {remaining_total_min % 60}m 后下班"
        else:
            remaining_str = f"{remaining_total_min}分后下班"

        worked_sec = (self._elapsed_in_period(now_t, self.am_start, self.am_end) +
                      self._elapsed_in_period(now_t, self.pm_start, self.pm_end))
        worked_h = int(worked_sec // 3600)
        worked_m = int((worked_sec % 3600) // 60)

        d = self.coin_engine.get_display()

        if now_t >= self.pm_end:
            self.info_label.setStyleSheet("""
                QLabel { background: rgba(140,10,10,230); border-radius: 14px;
                         padding: 10px 16px; border: 2px solid rgba(255,40,40,180); }
            """)
            dismiss_link = ("<a href='dismiss_offwork' style='text-decoration:none;"
                            "color:#ff8080;font-size:11px;'>✕ 知道了</a>")
            info_html = f"""
            <div style='font-family:Microsoft YaHei,Arial;text-align:center;'>
                <div style='font-size:18px;font-weight:bold;color:#fff;
                            letter-spacing:3px;margin-bottom:4px;'>
                    {now.strftime('%H:%M:%S')}</div>
                <div style='font-size:14px;color:#ffee00;font-weight:bold;margin-bottom:4px;'>
                    🚨 下班了！关电脑！回家！</div>
                <div style='font-size:11px;color:#ffbbbb;line-height:1.8;'>
                    今日金币 <span style='color:#ffdd57;font-weight:bold;font-size:15px;'>
                    🪙 {d['today_total']:.2f}</span></div>
                <div style='font-size:10px;color:#a06060;'>
                    基础 {d['today_base']:.2f} + 绩效 {d['today_bonus']:.2f}</div>
                <div style='margin-top:6px;'>{dismiss_link}</div>
            </div>"""
        else:
            xp_bar = self._xp_bar_str(d["xp_pct"])
            pts_hint = (f" <span style='color:#ffaa22;font-size:10px;'>"
                        f"✦{d['talent_pts']}点</span>" if d["talent_pts"] else "")
            self.info_label.setStyleSheet("""
                QLabel { background: rgba(4,6,18,220); border-radius: 14px;
                         padding: 10px 16px; border: 1px solid rgba(0,100,200,70); }
            """)
            info_html = f"""
            <div style='font-family:Microsoft YaHei,Arial;text-align:center;'>
                <div style='font-size:20px;font-weight:bold;color:#00d4ff;
                            letter-spacing:3px;margin-bottom:4px;'>
                    {now.strftime('%H:%M:%S')}</div>
                <div style='font-size:12px;color:#6090c0;margin-bottom:6px;'>{status}</div>
                <div style='height:1px;background:rgba(0,150,255,20);margin:4px 0 6px 0;'></div>
                <div style='font-size:11px;color:#8090b0;line-height:2;'>
                    ⏱ {worked_h}h {worked_m}m &nbsp;·&nbsp; 🏃 {remaining_str}</div>
                <div style='font-size:13px;color:#90a8c8;margin-top:2px;'>
                    🪙 <span style='color:#ffdd57;font-weight:bold;font-size:16px;'>
                    {d['today_total']:.2f}</span>
                    &nbsp;⭐ <span style='color:#00c8ff;'>Lv.{d['level']}</span>{pts_hint}</div>
                <div style='font-size:10px;color:#7090b0;margin-top:1px;'>
                    {xp_bar}
                    <span>&nbsp;升级还差 {max(0, d['xp_next'] - d['xp']):.0f} 金币</span></div>
                <div style='margin-top:5px;'>{self._equip_slots_html()}</div>
            </div>"""

        self.info_label.setText(info_html)

    def switch_gif(self, filename, force=False):
        if self.live_pet_enabled:
            mood_map = {
                "angry.gif": "intense", "SLEEPY.gif": "tired", "MUSIC.gif": "music",
                "xiaban.gif": "off_work", "manei.gif": "focused",
            }
            basename = os.path.basename(filename)
            self.live_pet.set_pet_size(self.pet_size)
            self.live_pet.set_mood(mood_map.get(basename, self._live_mood))
            self.current_gif = filename
            panel_w = max(self.pet_size + 24, 150)
            self.info_label.setMaximumWidth(panel_w)
            self.adjustSize()
            return
        if self.current_gif == filename and not force:
            return
        self.movie = QMovie(filename)
        self.movie.jumpToFrame(0)
        orig = self.movie.currentPixmap().size()
        if orig.width() > 0 and orig.height() > 0:
            ratio = orig.height() / orig.width()
            if orig.width() >= orig.height():
                scaled = QSize(self.pet_size, int(self.pet_size * ratio))
            else:
                scaled = QSize(int(self.pet_size / ratio), self.pet_size)
            self.movie.setScaledSize(scaled)
        self.pet_label.setMovie(self.movie)
        self.movie.start()
        self.current_gif = filename
        # 让 info_label 宽度跟随 pet_size，避免换小尺寸时状态框不缩小
        panel_w = max(self.pet_size + 24, 150)
        self.info_label.setMaximumWidth(panel_w)
        self.adjustSize()
        if hasattr(self, 'chat_input'):
            self._update_child_positions()

    def contextMenuEvent(self, event):
        from PyQt6.QtCore import QCoreApplication
        menu = QMenu()
        menu.addAction("设置参数", lambda: SettingsDialog(self).exec())
        menu.addAction("查看今日记录", lambda: RecordsDialog(self).exec())
        menu.addAction("工作日历", lambda: WorkCalendarDialog(self).exec())
        menu.addAction("立即预览日报", lambda: self._trigger_daily_confirm())
        menu.addAction("立即生成周报", lambda: self._trigger_weekly_report())
        menu.addSeparator()
        menu.addAction("🪙 金币商店 / 背包", lambda: CoinShopDialog(self.coin_engine, self).exec())
        menu.addAction("⭐ 天赋树", lambda: TalentDialog(self.coin_engine, self).exec())
        menu.addSeparator()
        menu.addAction("退出", QCoreApplication.instance().quit)
        menu.exec(event.globalPos())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._update_child_positions()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    pet = DesktopPet()
    sys.exit(app.exec())
