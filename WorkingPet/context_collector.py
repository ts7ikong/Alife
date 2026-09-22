"""
context_collector.py
采集工作相关的上下文数据（ContextCollector.start_all() 默认启动）：
  1. 中文键盘输入（IME上屏内容）
  2. 桌面截图 → 豆包视觉分析 → 存文字描述

MusicCollector（网易云当前播放歌名+歌词）保留类定义供未来需要时使用，
但默认不启动 —— 属于娱乐内容，不采集、也不进日报。
"""
import json
import os
import threading
import time as _time
import random
from datetime import datetime

# ── 存储文件 ──────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
INPUT_LOG_FILE    = os.path.join(_BASE, "input_log.json")
CONTEXT_LOG_FILE  = os.path.join(_BASE, "context_log.json")  # 截图描述 + 歌曲
ACTIVITY_LOG_FILE = os.path.join(_BASE, "activity_log.json") # 2分钟活动摘要时间线


# ══════════════════════════════════════════════════════════════
# 1. 中文键盘输入采集
# ══════════════════════════════════════════════════════════════
class InputCollector:
    """
    每秒轮询剪贴板，检测新出现的中文内容并记录。
    输入法上屏时内容会写入剪贴板，这是在 Windows 上
    捕获中文输入最可靠的方式。
    过滤规则：
      - 至少含 3 个汉字
      - 长度不超过 200 字（排除复制的大段文本）
      - 和上一条不重复
    """
    MIN_CHINESE = 3
    MAX_LEN     = 200
    MAX_ENTRIES = 50

    def __init__(self):
        self._segments: list[str] = []
        self._last_clip: str = ""
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not os.path.exists(INPUT_LOG_FILE):
            return
        try:
            data = json.loads(open(INPUT_LOG_FILE, encoding="utf-8").read())
            if data.get("date") == self._today:
                self._segments = data.get("segments", [])
        except Exception:
            pass

    def _save(self):
        try:
            with open(INPUT_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump({"date": self._today,
                           "segments": self._segments[-self.MAX_ENTRIES:]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _is_chinese(self, s: str) -> bool:
        return sum(1 for c in s if "\u4e00" <= c <= "\u9fff") >= self.MIN_CHINESE

    def _poll(self):
        """读一次剪贴板，有新中文内容就记录"""
        try:
            import pyperclip
            text = pyperclip.paste().strip()
            if not text or text == self._last_clip:
                return
            if len(text) > self.MAX_LEN:
                return
            if not self._is_chinese(text):
                return

            self._last_clip = text
            today = datetime.now().strftime("%Y-%m-%d")
            with self._lock:
                if today != self._today:
                    self._today = today
                    self._segments = []
                if not self._segments or self._segments[-1] != text:
                    self._segments.append(text)
                    self._save()
        except Exception:
            pass

    def get_recent(self, n: int = 5) -> list[str]:
        with self._lock:
            return self._segments[-n:]

    def start(self):
        def loop():
            while True:
                self._poll()
                _time.sleep(1)
        t = threading.Thread(target=loop, daemon=True)
        t.start()


# ══════════════════════════════════════════════════════════════
# 2. 网易云音乐当前歌名
# ══════════════════════════════════════════════════════════════
class MusicCollector:
    """
    每30秒读一次网易云窗口标题，解析出歌名，
    歌名变化时自动拉取歌词并随机存两句。
    """
    MAX_HISTORY = 20

    def __init__(self):
        self._history: list[str] = []
        self._current: str = ""
        self._current_lyrics: list[str] = []
        self._lock = threading.Lock()
        self.on_song_change = None  # 歌曲变化时的回调 fn(song, lyrics)

    def _get_netease_title(self) -> str:
        """通过进程名找 cloudmusic.exe，取格式为「歌名 - 歌手」的窗口标题"""
        try:
            import ctypes, ctypes.wintypes, psutil
            user32 = ctypes.windll.user32
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            def cb(hwnd, _):
                pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    p = psutil.Process(pid.value)
                    if p.name().lower() != "cloudmusic.exe":
                        return True
                    l = user32.GetWindowTextLengthW(hwnd)
                    if l < 3:
                        return True
                    b = ctypes.create_unicode_buffer(l + 1)
                    user32.GetWindowTextW(hwnd, b, l + 1)
                    title = b.value.strip()
                    # 排除已知非歌名窗口
                    if title and " - " in title and title not in (
                        "桌面歌词", "桌面歌词解锁", "迷你播放器",
                        "MSCTFIME UI", "Default IME"
                    ) and "GDI+" not in title:
                        found.append(title)
                except Exception:
                    pass
                return True

            user32.EnumWindows(cb, 0)
            return found[0] if found else ""
        except Exception:
            return ""

    def _parse_song(self, title: str) -> str:
        """格式：歌名 - 歌手，取第一段"""
        parts = title.split(" - ")
        return parts[0].strip() if parts else ""

    def _fetch_lyrics(self, song_name: str) -> list[str]:
        """根据歌名查ID再拉歌词，返回随机2句纯文字歌词"""
        import urllib.request, urllib.parse, re
        try:
            # 第一步：搜索歌曲拿ID
            url = ("http://music.163.com/api/search/get?s="
                   + urllib.parse.quote(song_name)
                   + "&type=1&limit=1")
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://music.163.com/"
            })
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            songs = data.get("result", {}).get("songs", [])
            if not songs:
                return []
            song_id = songs[0]["id"]

            # 第二步：拉歌词
            lyric_url = f"http://music.163.com/api/song/lyric?id={song_id}&lv=1"
            req2 = urllib.request.Request(lyric_url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://music.163.com/"
            })
            with urllib.request.urlopen(req2, timeout=8) as r:
                ldata = json.loads(r.read())
            raw = ldata.get("lrc", {}).get("lyric", "")
            if not raw:
                return []

            # 解析 LRC 格式，去掉时间戳，过滤空行和作词/作曲行
            lines = []
            for line in raw.splitlines():
                text = re.sub(r"\[.*?\]", "", line).strip()
                if (text and len(text) >= 4
                        and not any(k in text for k in
                                    ["作词", "作曲", "编曲", "制作", "出品", "混音"])):
                    lines.append(text)

            if not lines:
                return []
            # 随机取2句不相邻的歌词
            if len(lines) <= 2:
                return lines
            idx = random.randint(0, len(lines) - 2)
            return [lines[idx], lines[idx + 1]]

        except Exception:
            return []

    def poll(self):
        """轮询一次，歌名变化时拉歌词"""
        title = self._get_netease_title()
        if not title:
            return
        song = self._parse_song(title)
        if not song:
            return
        with self._lock:
            changed = song != self._current
            if changed:
                self._current = song
                if song not in self._history:
                    self._history.append(song)
                    if len(self._history) > self.MAX_HISTORY:
                        self._history.pop(0)

        # 歌名变化时在锁外拉歌词（网络请求不占锁）
        if changed:
            lyrics = self._fetch_lyrics(song)
            with self._lock:
                self._current_lyrics = lyrics
            # 触发外部回调
            if self.on_song_change:
                try:
                    self.on_song_change(song, lyrics)
                except Exception:
                    pass

    def get_current(self) -> str:
        with self._lock:
            return self._current

    def get_current_lyrics(self) -> list[str]:
        with self._lock:
            return self._current_lyrics[:]

    def get_history(self, n: int = 5) -> list[str]:
        with self._lock:
            return self._history[-n:]

    def start(self):
        def loop():
            while True:
                self.poll()
                _time.sleep(30)
        t = threading.Thread(target=loop, daemon=True)
        t.start()


# ══════════════════════════════════════════════════════════════
# 3. 桌面截图 + 豆包视觉分析
# ══════════════════════════════════════════════════════════════
class ScreenshotCollector:
    """
    每隔随机 15-30 分钟截一次屏，调豆包视觉接口分析内容，
    只存文字描述，不存图片，保护隐私。
    """
    MAX_DESCRIPTIONS = 10

    def __init__(self, api_key: str = "", endpoint_id: str = ""):
        self._api_key     = api_key
        self._endpoint_id = endpoint_id
        self._api_url     = ""
        self._descriptions: list[dict] = []   # [{time, text}]
        self._lock = threading.Lock()
        self._enabled = False
        self._load()

    def _load(self):
        if not os.path.exists(CONTEXT_LOG_FILE):
            return
        try:
            data = json.loads(open(CONTEXT_LOG_FILE, encoding="utf-8").read())
            self._descriptions = data.get("screenshots", [])
        except Exception:
            pass

    def _save(self):
        try:
            existing = {}
            if os.path.exists(CONTEXT_LOG_FILE):
                existing = json.loads(open(CONTEXT_LOG_FILE, encoding="utf-8").read())
            existing["screenshots"] = self._descriptions[-self.MAX_DESCRIPTIONS:]
            with open(CONTEXT_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def update_config(self, api_key: str, endpoint_id: str, enabled: bool, api_url: str = ""):
        self._api_key     = api_key
        self._endpoint_id = endpoint_id
        self._enabled     = enabled
        self._api_url     = api_url

    def _take_and_analyze(self):
        """截图并调 AI 分析，结果存为文字"""
        if not self._enabled or not self._api_key or not self._endpoint_id:
            return
        try:
            import base64, io
            from PIL import ImageGrab
            import urllib.request

            # 截图并压缩（避免图太大，分辨率压到1280宽）
            img = ImageGrab.grab()
            w, h = img.size
            if w > 1280:
                img = img.resize((1280, int(h * 1280 / w)))

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            b64 = base64.b64encode(buf.getvalue()).decode()

            payload = json.dumps({
                "model": self._endpoint_id,
                "max_tokens": 150,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": ("简短描述这个桌面在做什么，30字以内，"
                                     "不要提及任何账号密码或敏感信息，"
                                     "只说大概在做什么工作或活动。")
                        }
                    ]
                }]
            }).encode("utf-8")

            from work_log import _DOUBAO_URL
            req = urllib.request.Request(
                self._api_url or _DOUBAO_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                desc = result["choices"][0]["message"]["content"].strip()

            if desc:
                with self._lock:
                    self._descriptions.append({
                        "time": datetime.now().strftime("%H:%M"),
                        "text": desc
                    })
                self._save()

        except Exception:
            pass  # 静默失败

    def get_recent(self, n: int = 3) -> list[str]:
        with self._lock:
            return [d["text"] for d in self._descriptions[-n:]]

    def start(self):
        self._enabled = True

        def loop():
            # 启动后先等5分钟再开始截图，避免刚打开就截
            _time.sleep(300)
            while True:
                self._take_and_analyze()
                # 随机间隔 15-30 分钟
                _time.sleep(random.randint(15 * 60, 30 * 60))

        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def stop(self):
        self._enabled = False


# ══════════════════════════════════════════════════════════════
# 4. 实时活动监控（每30秒采样，每2分钟生成活动摘要）
# ══════════════════════════════════════════════════════════════
class ActivityMonitor:
    """
    每30秒采样一次前台窗口标题，每2分钟汇总生成"你在做什么"的标签，
    供面板状态文字实时显示。优先调用豆包 API 生成自然语言描述，
    未配置 API 时回退到本地规则解析。
    """
    SAMPLE_INTERVAL  = 30   # 采样间隔（秒）
    SUMMARY_INTERVAL = 120  # 摘要生成间隔（秒）
    MAX_SAMPLES      = 8    # 最多保留最近8条采样（约4分钟）

    # (关键词列表, 可读标签) — 从上到下优先匹配
    _APP_RULES = [
        (["visual studio code", "vscode", "cursor", "windsurf"], "写代码"),
        (["pycharm", "intellij", "idea", "webstorm", "goland", "clion", "rider", "android studio"], "写代码"),
        (["微信", "wechat"], "微信"),
        (["钉钉", "dingtalk"], "钉钉"),
        (["飞书", "feishu", "lark"], "飞书"),
        (["zoom", "腾讯会议", "tencent meeting", "teams", "webex", "腾讯视频"], "视频会议"),
        (["outlook", "foxmail", "thunderbird"], "邮件"),
        (["word", "excel", "powerpoint", "wps", "金山文档"], "编辑文档"),
        (["postman", "apifox", "swagger", "insomnia", "apipost"], "接口调试"),
        (["figma", "sketch", "photoshop", "illustrator", "xd"], "设计"),
        (["notion", "obsidian", "typora", "onenote", "有道云笔记", "印象笔记"], "写文档"),
        (["xmind", "mindmaster", "mindmanager"], "整理思路"),
        (["slack", "discord"], "团队沟通"),
        (["chrome", "google chrome", "edge", "msedge", "firefox", "chromium"], "浏览网页"),
        (["cmd", "命令提示符", "powershell", "terminal", "bash", "wsl", "mintty"], "命令行"),
    ]
    _SKIP_KW = {"WorkingPet", "桌宠", "Python"}
    # 这些内容不需要交给模型判断；命中即从日报候选中隔离。
    _ENTERTAINMENT_KW = (
        "抖音", "douyin", "tiktok", "bilibili", "哔哩哔哩", "b站", "小红书",
        "微博", "知乎", "淘宝", "京东", "拼多多", "steam", "wegame", "游戏",
        "直播", "综艺", "电视剧", "电影", "小说", "斗鱼", "虎牙",
    )
    _WORK_SIGNALS = ("写代码", "命令行", "visual studio code", "pycharm", "intellij", "vscode", "cursor",
                     "windsurf", "terminal", "powershell", "文档", "excel", "word")

    def __init__(self):
        self._samples: list[str] = []
        self._summary = ""
        self._lock = threading.Lock()
        self._api_key = ""
        self._endpoint_id = ""
        self._api_url = ""
        self._on_summary_callback = None   # 新摘要产生时的通知回调

    def set_on_summary_callback(self, fn):
        with self._lock:
            self._on_summary_callback = fn

    def update_config(self, api_key: str, endpoint_id: str, api_url: str = ""):
        self._api_key = api_key
        self._endpoint_id = endpoint_id
        self._api_url = api_url

    @staticmethod
    def _get_active_title() -> str:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return ""
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value.strip()
        except Exception:
            return ""

    def _label_from_title(self, title: str) -> str:
        """将窗口标题解析为可读活动标签"""
        tl = title.lower()
        for keywords, label in self._APP_RULES:
            if any(kw in tl for kw in keywords):
                if label == "写代码":
                    # 提取正在编辑的文件名（VS Code 格式："文件名 - 项目 - VS Code"）
                    parts = [p.strip() for p in title.split(" - ") if p.strip()]
                    if parts and "." in parts[0] and len(parts[0]) < 40:
                        return f"写代码 ({parts[0]})"
                if label == "浏览网页":
                    # 提取网页标题（格式："页面标题 - 网站名 - Chrome"）
                    parts = [p.strip() for p in title.split(" - ") if p.strip()]
                    if len(parts) >= 2 and len(parts[0]) < 20:
                        return f"浏览 {parts[0]}"
                return label
        # 未匹配：取标题最后一段作为应用名
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        name = parts[-1] if parts else title
        return name[:12] if name else ""

    def _save_entry(self, summary: str, kind: str = "uncertain", confidence: str = "low",
                    evidence: list[str] | None = None):
        """保存活动及其可信度；日报只消费高置信 work 条目。"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            data = {}
            if os.path.exists(ACTIVITY_LOG_FILE):
                try:
                    data = json.loads(open(ACTIVITY_LOG_FILE, encoding="utf-8").read())
                except Exception:
                    data = {}
            if data.get("date") != today:
                data = {"date": today, "entries": []}
            data["entries"].append({
                "time":    datetime.now().strftime("%H:%M"),
                "summary": summary,
                "kind": kind,
                "confidence": confidence,
                "evidence": (evidence or [])[-4:],
            })
            data["entries"] = data["entries"][-200:]
            with open(ACTIVITY_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _update_summary(self):
        with self._lock:
            samples     = list(self._samples)
            api_key     = self._api_key
            endpoint_id = self._endpoint_id
            api_url     = self._api_url
        if not samples:
            return

        summary = ""
        entertainment = [t for t in samples if any(k in t.lower() for k in self._ENTERTAINMENT_KW)]
        candidates = [t for t in samples if t not in entertainment]
        kind, confidence = "uncertain", "low"

        # 整个片段都是娱乐时，直接落盘标记，不进入模型推断和日报。
        if samples and len(entertainment) == len(samples):
            self._save_entry("", "non_work", "high", samples)
            with self._lock:
                self._summary = ""
            return

        # 仅浏览网页而无明确工作信号，不能凭标题猜成工作。
        has_work_signal = any(any(k in t.lower() for k in self._WORK_SIGNALS)
                              for t in candidates)
        if not has_work_signal:
            self._save_entry("", "uncertain", "low", candidates)
            with self._lock:
                self._summary = ""
            return

        # ── 优先：调豆包 API 生成自然语言描述 ──
        if api_key and endpoint_id:
            try:
                from work_log import _doubao_request
                titles = "\n".join(f"- {t}" for t in candidates)
                prompt = (
                    f"以下是用户过去2分钟内依次使用的窗口标题：\n{titles}\n\n"
                    "判断用户在做什么，格式：动词 + 具体项目名，例如：\n"
                    "「调试佳易鑫人员定位系统」「对接广西烟花爆竹预警接口」「开发 WorkingPet 桌宠」\n\n"
                    "规则：\n"
                    "- 只有能从标题中明确推断出具体项目名时才输出\n"
                    "- 不提工具名、文件名、配置项等细节\n"
                    "- 娱乐、视频、购物、社交、泛浏览内容一律返回空字符串\n"
                    "- 如果无法确定具体项目和动作，返回空字符串；不得猜测或补全\n\n"
                    "只输出结果，不确定就返回空字符串，不加任何解释。"
                )
                result = _doubao_request(
                    api_key, endpoint_id,
                    [{"role": "user", "content": prompt}],
                    max_tokens=60,
                    api_url=api_url
                )
                if result and result.strip():
                    summary = result.strip()
                    kind, confidence = "work", "high"
            except Exception:
                pass  # 降级到本地解析

        # ── 兜底：本地规则解析 ──
        if not summary:
            counts: dict[str, int] = {}
            for t in candidates:
                lb = self._label_from_title(t)
                # 泛网页浏览不是日报事实，只保留确定的工作工具活动。
                if lb and not lb.startswith("浏览") and lb not in ("微信", "钉钉"):
                    counts[lb] = counts.get(lb, 0) + 1
            if counts:
                top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:2]
                summary = "  ·  ".join(lb for lb, _ in top)
                kind, confidence = "work", "medium"

        with self._lock:
            self._summary = summary
            callback = self._on_summary_callback if summary else None
        # 无论是否为空都落盘，空条目在记录弹窗里显示为待填写
        self._save_entry(summary, kind, confidence, candidates)
        if callback:
            try:
                callback(summary)
            except Exception:
                pass

    def get_summary(self) -> str:
        with self._lock:
            return self._summary

    def start(self):
        def loop():
            last_summary_at = _time.time()
            while True:
                try:
                    title = self._get_active_title()
                    if title and not any(k in title for k in self._SKIP_KW):
                        with self._lock:
                            self._samples.append(title)
                            self._samples = self._samples[-self.MAX_SAMPLES:]
                except Exception:
                    pass
                now = _time.time()
                if now - last_summary_at >= self.SUMMARY_INTERVAL:
                    self._update_summary()
                    last_summary_at = now
                _time.sleep(self.SAMPLE_INTERVAL)

        threading.Thread(target=loop, daemon=True, name="ActivityMonitor").start()


# ══════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════
class ContextCollector:
    """
    统一管理四个采集器，对外提供 get_context_snapshot() 接口，
    返回一个可以直接塞进 prompt 的上下文字符串。
    """
    def __init__(self):
        self.input_col   = InputCollector()
        self.music_col   = MusicCollector()
        self.screen_col  = ScreenshotCollector()
        self.activity    = ActivityMonitor()

    def start_all(self, screenshot_enabled: bool = False,
                  api_key: str = "", endpoint_id: str = "", api_url: str = ""):
        self.input_col.start()
        self.activity.update_config(api_key, endpoint_id, api_url)
        self.activity.start()
        # 音乐/歌词是娱乐内容，不采集，避免混入工作日报
        if screenshot_enabled:
            self.screen_col.update_config(api_key, endpoint_id, True, api_url)
            self.screen_col.start()

    def update_screenshot_config(self, enabled: bool,
                                  api_key: str, endpoint_id: str, api_url: str = ""):
        self.screen_col.update_config(api_key, endpoint_id, enabled, api_url)
        if enabled and not self.screen_col._enabled:
            self.screen_col.start()
        elif not enabled:
            self.screen_col.stop()

    def update_activity_config(self, api_key: str, endpoint_id: str, api_url: str = ""):
        self.activity.update_config(api_key, endpoint_id, api_url)

    def get_context_snapshot(self) -> str:
        """
        返回当前上下文摘要字符串，供日报生成等场景使用。
        只包含工作相关信息（实时活动、中文输入、截图描述），
        音乐/歌词等娱乐内容不采集、也不会出现在这里。
        示例输出：
          正在：写代码 (manei_manager.py)  ·  浏览网页
          最近输入：你觉得这个方案怎么样 / 下午开个会
          桌面：在用 IDE 写代码，有多个文件标签
        """
        parts = []

        # 实时活动摘要
        activity = self.activity.get_summary()
        if activity:
            parts.append(f"正在：{activity}")

        # 最近中文输入
        inputs = self.input_col.get_recent(2)
        if inputs:
            parts.append(f"最近输入：{'  /  '.join(inputs)}")

        # 截图描述
        screens = self.screen_col.get_recent(1)
        if screens:
            parts.append(f"桌面：{screens[0]}")

        return "\n".join(parts)
