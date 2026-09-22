# WorkingPet — AI 桌面宠物

## 项目概述

一个运行在 Windows 桌面的 AI 宠物助手，用 PyQt6 实现无边框悬浮窗口。核心功能：
- 工作记录（记录 → `work_log.json`，按日期分组）
- AI 周报生成（豆包流式 API）
- 今日薪资实时计算
- 根据鼠标/键盘活动自动切换 GIF 状态
- 上下文感知主动说话（音乐、截图、输入内容）
- 加班追踪、钉钉一键启动

## 文件结构

| 文件 | 职责 |
|---|---|
| `manei_manager.py` | 主入口，`DesktopPet` 主窗口类，所有 UI 和状态机 |
| `work_log.py` | 工作日志 CRUD + 豆包 API 封装（`_doubao_request`） |
| `context_collector.py` | 三路上下文采集：剪贴板中文输入、网易云歌名、桌面截图分析。`DesktopPet.__init__` 通过 `_start_background_collectors()` 启动 |
| `data_collector.py` | 后台行为采集：聊天内容剪贴板监听、屏幕 OCR、应用使用时长、鼠标活跃度、Chrome 浏览器历史。同样由 `_start_background_collectors()` 启动 |
| `config.example.json` | 配置模板，复制为 `config.json` 后按需修改（`config.json` 已加入 `.gitignore`，不再入库） |
| `config.json` | 本地配置（上下班时间、月薪、pet_size、API Key 等），仅本地保留，不提交 |
| `work_log.json` | 工作记录数据，格式 `{date: [{time, content}]}`，仅本地保留 |
| `input_log.json` | 今日中文输入记录（`InputCollector` 写入），仅本地保留 |
| `context_log.json` | 截图分析文字描述（`ScreenshotCollector` 写入），仅本地保留 |
| `chat_input_log.json` / `raw_behavior.json` / `browser_history.json` | `data_collector.py` 产出的聊天/行为/浏览历史数据，仅本地保留 |
| `*.gif` | 状态动画素材（manei/angry/SLEEPY/MUSIC/xiaban） |

详见 `功能描述.md`（现有功能全景）和 `代码规范.md`（编码约定）。

## 运行方式

```powershell
python manei_manager.py
```

依赖：`PyQt6`, `pynput`, `psutil`, `pyperclip`, `Pillow`（截图功能需要）

## 架构关键点

### GIF 状态机（`manei_manager.py:GIF_*` 常量）

```
GIF_IDLE    → manei.gif      # 正常工作
GIF_ANGRY   → angry.gif      # 高频打字（连续操作 > activity_angry_threshold 秒）
GIF_SLEEPY  → SLEEPY.gif     # 空闲超过 idle_threshold_minutes 分钟
GIF_MUSIC   → MUSIC.gif      # 检测到 QQMusic.exe 或 cloudmusic.exe
GIF_XIABAN  → xiaban.gif     # 当前时间 >= pm_end
```

新增 GIF：在 `GIF_*` 常量处添加文件名，在 `_STATE_LINES` 字典添加台词，在 `_preload_gifs()` 的列表里添加文件名，在 `_get_state()` 里加分支。

### AI API

使用**豆包（火山引擎 ARK）**，端点兼容 OpenAI 格式：
- URL: `https://ark.cn-beijing.volces.com/api/v3/chat/completions`
- 认证: `Authorization: Bearer <api_key>`
- model 字段填 `endpoint_id`（格式 `ep-XXXXXXXX-XXXXX`）

两个底层函数：
- `_doubao_request()` — 普通同步请求
- `_doubao_stream()` — 流式请求，通过 `on_chunk` 回调逐块返回

### 跨线程通信

所有 AI 调用在 `threading.Thread(daemon=True)` 里执行，结果通过 `Bridge`（`QObject` + `pyqtSignal`）发回主线程。不要直接在子线程里操作 Qt 控件。

### 心情系统

`self.mood` 范围 0-100，四档：low/calm/happy/hype。台词从 `_STATE_LINES` / `_TIME_LINES` 按档位随机取。`_mod_mood(delta)` 修改并延迟 5 秒写盘（避免频繁 IO）。

### 主动说话

`_check_proactive_talk()` 每 60 秒触发，用 `_time_event_flags` 防止同一天同一事件重复。AI 台词通过 `generate_proactive_line()` 在后台线程生成，用 `__SAY__:` 前缀区分普通回复。

### 配置持久化

`load_config()` / `save_config()` 读写 `config.json`。`DesktopPet.__init__` 启动时读取，`SettingsDialog.save()` 保存。不需要重启即可生效（部分参数需要调用 `switch_gif(force=True)` 刷新）。

## 开发约定

- 添加新按钮到 `HoverPanel._BTN_CFG` 列表，同时在 `HoverPanel` 上加对应 `pyqtSignal`，在 `DesktopPet.__init__` 里 connect。
- `BubbleWidget.show_message(text, show_dingtalk, auto_hide_ms)` — `auto_hide_ms=0` 表示不自动消失。
- `ChatInput.submitted` 信号 → `DesktopPet._on_user_input()` 处理用户文字输入。
- 添加新的时间节点主动说话：在 `_TIME_LINES` 加 key，在 `_check_proactive_talk()` 加触发条件，调用 `_fire(key)`。
- 意图识别扩展：在 `understand_intent()` 的 system prompt 里加新意图说明，在 `_on_user_input()` 的 `elif intent == ...` 分支里处理。
