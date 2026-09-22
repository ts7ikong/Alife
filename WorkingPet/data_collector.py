"""
data_collector.py
后台数据采集模块。所有采集在独立守护线程中运行，
任何单个模块异常不影响其他模块和主程序。

采集内容：
  1. 聊天内容监听（回车键拦截 + 剪贴板读取）
  2. 截图 + OCR（EasyOCR，Reader 只初始化一次）
  3. 应用使用时长（win32gui + psutil）
  4. 鼠标活跃度（pynput，每分钟汇总）
  5. Chrome 浏览器历史（复制后读取 SQLite）
"""
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime

import psutil
import win32gui
import win32process
from pynput import mouse as _mouse
from pynput.keyboard import Controller as _KbCtrl, Key as _Key

_BASE = os.path.dirname(os.path.abspath(__file__))
CHAT_LOG_FILE   = os.path.join(_BASE, "chat_input_log.json")
OCR_LOG_FILE    = os.path.join(_BASE, "ocr_log.json")
BEHAVIOR_FILE   = os.path.join(_BASE, "raw_behavior.json")
BROWSER_FILE    = os.path.join(_BASE, "browser_history.json")
CHROME_HISTORY  = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    r"Google\Chrome\User Data\Default\History",
)

_CHAT_KEYWORDS = ("微信", "wechat", "钉钉", "dingtalk", "飞书", "chrome", "edge", "firefox")
_CHAT_NAME_MAP = {
    "微信": "微信", "wechat": "微信",
    "钉钉": "钉钉", "dingtalk": "钉钉",
    "飞书": "飞书",
    "chrome": "Chrome", "edge": "Edge", "firefox": "Firefox",
}

_behavior_lock = threading.Lock()
_kb_ctrl       = _KbCtrl()

# 聊天监听全局状态
_capturing  = False   # 全局锁，防止回车拦截重入
_forwarding = False   # 标记下一次回车是我们补发的，不拦截
_ocr_reader = None    # EasyOCR Reader 单例


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════
def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _is_chat_window(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _CHAT_KEYWORDS)


def _get_window_name(title: str) -> str:
    tl = title.lower()
    for kw, name in _CHAT_NAME_MAP.items():
        if kw in tl:
            return name
    return title[:30]


# ══════════════════════════════════════════════════════════════
# Win32 剪贴板操作
# ══════════════════════════════════════════════════════════════
def _get_clipboard_text() -> str:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except Exception:
            return ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def _set_clipboard_text(text: str):
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        if text:
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        win32clipboard.CloseClipboard()
    except Exception:
        pass


def _ctrl_a_c():
    """模拟 Ctrl+A 全选，再 Ctrl+C 复制"""
    with _kb_ctrl.pressed(_Key.ctrl):
        _kb_ctrl.tap('a')
    time.sleep(0.05)
    with _kb_ctrl.pressed(_Key.ctrl):
        _kb_ctrl.tap('c')


# ══════════════════════════════════════════════════════════════
# 1. 聊天内容监听
# ══════════════════════════════════════════════════════════════
def _save_chat_entry(window_name: str, text: str):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = _read_json(CHAT_LOG_FILE, {})
        if data.get("date") != today:
            data = {"date": today, "entries": []}
        data["entries"].append({
            "time":   datetime.now().strftime("%H:%M:%S"),
            "window": window_name,
            "text":   text[:500],
        })
        _write_json(CHAT_LOG_FILE, data)
    except Exception:
        pass


def _chat_enter_handler(event):
    """keyboard hook 回调，拦截回车键读取聊天输入"""
    global _capturing, _forwarding

    if _forwarding:
        _forwarding = False
        return False  # 不拦截：这是我们补发的回车

    if _capturing:
        return False  # 不拦截：防止重入

    # Shift+Enter 是换行，不是提交，直接放行
    try:
        import keyboard as _kb
        if _kb.is_pressed('shift'):
            return False
    except Exception:
        pass

    try:
        hwnd  = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
    except Exception:
        return False

    if not _is_chat_window(title):
        return False  # 非聊天窗口，直接放行

    _capturing = True
    try:
        old_clip = _get_clipboard_text()
        _ctrl_a_c()
        time.sleep(0.1)
        text = _get_clipboard_text()
        _set_clipboard_text(old_clip)

        if len(text) > 1:
            _save_chat_entry(_get_window_name(title), text)
    except Exception:
        pass
    finally:
        _capturing = False

    _forwarding = True
    try:
        import keyboard
        keyboard.send('enter')
    except Exception:
        pass


def _start_chat_monitor():
    try:
        import keyboard
        keyboard.hook_key('enter', _chat_enter_handler, suppress=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# 2. 截图 + OCR
# ══════════════════════════════════════════════════════════════
def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        _ocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)
    return _ocr_reader


def _save_ocr_entry(text: str):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = _read_json(OCR_LOG_FILE, {})
        if data.get("date") != today:
            data = {"date": today, "entries": []}
        data["entries"].append({
            "time": datetime.now().strftime("%H:%M"),
            "text": text[:2000],
        })
        _write_json(OCR_LOG_FILE, data)
    except Exception:
        pass


def _start_ocr_collector(interval_minutes: int = 3):
    def loop():
        time.sleep(60)  # 启动后先等1分钟，避免冷启动 CPU 高负载
        while True:
            try:
                import numpy as np
                from PIL import ImageGrab

                img = ImageGrab.grab()
                w, h = img.size
                if w > 1280:
                    img = img.resize((1280, int(h * 1280 / w)))

                reader  = _get_ocr_reader()
                results = reader.readtext(np.array(img), detail=0, paragraph=True)
                text    = " ".join(str(r) for r in results).strip()
                if text:
                    _save_ocr_entry(text)
            except Exception:
                pass
            time.sleep(interval_minutes * 60)

    threading.Thread(target=loop, daemon=True, name="OcrCollector").start()


# ══════════════════════════════════════════════════════════════
# 3. 应用使用时长
# ══════════════════════════════════════════════════════════════
def _get_active_window_info():
    try:
        hwnd  = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc   = psutil.Process(pid)
        return proc.name(), title
    except Exception:
        return None, None


def _start_app_usage_collector(interval_sec: int = 30):
    def loop():
        while True:
            try:
                app_name, title = _get_active_window_info()
                if app_name:
                    today = datetime.now().strftime("%Y-%m-%d")
                    with _behavior_lock:
                        data = _read_json(BEHAVIOR_FILE, {})
                        day  = data.setdefault(today, {
                            "apps": {}, "window_titles": [], "mouse": []
                        })
                        day["apps"][app_name] = (
                            day["apps"].get(app_name, 0) + interval_sec
                        )
                        if title and title not in day["window_titles"]:
                            day["window_titles"].append(title)
                            day["window_titles"] = day["window_titles"][-100:]
                        _write_json(BEHAVIOR_FILE, data)
            except Exception:
                pass
            time.sleep(interval_sec)

    threading.Thread(target=loop, daemon=True, name="AppUsage").start()


# ══════════════════════════════════════════════════════════════
# 4. 鼠标活跃度（不记录坐标轨迹）
# ══════════════════════════════════════════════════════════════
_mouse_distance = 0.0
_mouse_clicks   = 0
_mouse_prev_x   = None
_mouse_prev_y   = None
_mouse_lock     = threading.Lock()


def _on_mouse_move(x, y):
    global _mouse_distance, _mouse_prev_x, _mouse_prev_y
    with _mouse_lock:
        if _mouse_prev_x is not None:
            dx = x - _mouse_prev_x
            dy = y - _mouse_prev_y
            _mouse_distance += (dx * dx + dy * dy) ** 0.5
        _mouse_prev_x = x
        _mouse_prev_y = y


def _on_mouse_click(x, y, button, pressed):
    global _mouse_clicks
    if pressed:
        with _mouse_lock:
            _mouse_clicks += 1


def _flush_mouse():
    global _mouse_distance, _mouse_clicks, _mouse_prev_x, _mouse_prev_y
    with _mouse_lock:
        dist   = int(_mouse_distance)
        clicks = _mouse_clicks
        _mouse_distance = 0.0
        _mouse_clicks   = 0
        _mouse_prev_x   = None
        _mouse_prev_y   = None

    if dist == 0 and clicks == 0:
        return
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        with _behavior_lock:
            data = _read_json(BEHAVIOR_FILE, {})
            day  = data.setdefault(today, {
                "apps": {}, "window_titles": [], "mouse": []
            })
            day["mouse"].append({
                "time":          datetime.now().strftime("%H:%M"),
                "move_distance": dist,
                "clicks":        clicks,
            })
            day["mouse"] = day["mouse"][-120:]  # 最多保留120条（2小时粒度）
            _write_json(BEHAVIOR_FILE, data)
    except Exception:
        pass


def _start_mouse_collector():
    listener = _mouse.Listener(on_move=_on_mouse_move, on_click=_on_mouse_click)
    listener.daemon = True
    listener.start()

    def flush_loop():
        while True:
            time.sleep(60)
            _flush_mouse()

    threading.Thread(target=flush_loop, daemon=True, name="MouseFlush").start()


# ══════════════════════════════════════════════════════════════
# 5. Chrome 浏览器历史
# ══════════════════════════════════════════════════════════════
_CHROME_EPOCH_OFFSET = 11644473600  # 1601-01-01 到 Unix epoch 的秒数


def _read_chrome_history(minutes: int = 30) -> list:
    if not os.path.exists(CHROME_HISTORY):
        return []

    tmp_path = tempfile.mktemp(suffix=".db")
    try:
        shutil.copy2(CHROME_HISTORY, tmp_path)
    except Exception:
        return []

    visits = []
    try:
        cutoff_unix   = datetime.now().timestamp() - minutes * 60
        cutoff_chrome = int((cutoff_unix + _CHROME_EPOCH_OFFSET) * 1e6)

        conn = sqlite3.connect(tmp_path)
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT urls.url, urls.title, visits.visit_time
            FROM visits JOIN urls ON visits.url = urls.id
            WHERE visits.visit_time > ?
            ORDER BY visits.visit_time DESC
            LIMIT 100
            """,
            (cutoff_chrome,),
        )
        seen = set()
        for url, title, _ in cur.fetchall():
            if url not in seen:
                seen.add(url)
                visits.append({"url": url, "title": title or ""})
        conn.close()
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    return visits


def _save_browser_entries(visits: list):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = _read_json(BROWSER_FILE, {})
        if data.get("date") != today:
            data = {"date": today, "entries": []}
        now_str = datetime.now().strftime("%H:%M")
        for v in visits:
            data["entries"].append({
                "time":  now_str,
                "url":   v["url"],
                "title": v["title"],
            })
        _write_json(BROWSER_FILE, data)
    except Exception:
        pass


def _start_browser_collector(interval_minutes: int = 30):
    def loop():
        time.sleep(300)  # 启动后5分钟再开始
        while True:
            try:
                visits = _read_chrome_history(30)
                if visits:
                    _save_browser_entries(visits)
            except Exception:
                pass
            time.sleep(interval_minutes * 60)

    threading.Thread(target=loop, daemon=True, name="BrowserHistory").start()


# ══════════════════════════════════════════════════════════════
# 统一启动入口
# ══════════════════════════════════════════════════════════════
def start_all(api_key: str = "", endpoint_id: str = ""):
    """启动所有后台采集线程，任何单个模块失败不影响其他模块。"""
    for fn in (
        # _start_chat_monitor 已禁用：keyboard.hook_key suppress=True 会全局拦截回车键，
        # 导致 CMD 等非聊天窗口的回车失效，用户体验破坏过大。
        lambda: _start_ocr_collector(interval_minutes=3),
        lambda: _start_app_usage_collector(interval_sec=30),
        _start_mouse_collector,
        lambda: _start_browser_collector(interval_minutes=30),
    ):
        try:
            fn()
        except Exception:
            pass
