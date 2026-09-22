import json
import os
from datetime import datetime, timedelta

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_log.json")

WEEKLY_REPORT_FORMAT = """标题：周报（YYYY年MM月DD日 - YYYY年MM月DD日）

开头句式固定为：
本周（XX年XX月XX日 - XX年XX月XX日）共完成X项工作，主要涉及[不超过4个关键词领域]等方面，整体工作有序推进。

分项用"-"开头，格式：
- [小结词]：[内容]

语言平实不加修饰词。
下周计划和需协调事项若无则写"暂无具体安排"和"暂无"，不加额外说明。"""


def load_logs() -> dict:
    if not os.path.exists(LOG_FILE):
        return {}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_logs(logs: dict):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def add_log(text: str):
    logs = load_logs()
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in logs:
        logs[today] = []
    logs[today].append({
        "time": datetime.now().strftime("%H:%M"),
        "content": text.strip()
    })
    save_logs(logs)
    return len(logs[today])


def replace_today_logs(entries: list):
    """用确认后的日报条目覆盖今天的记录，entries 是 [{"time":..., "content":...}, ...]"""
    logs = load_logs()
    today = datetime.now().strftime("%Y-%m-%d")
    logs[today] = entries
    save_logs(logs)


def get_week_logs() -> dict:
    logs = load_logs()
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    result = {}
    for i in range(7):
        day = monday + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        if key in logs and logs[key]:
            result[key] = logs[key]
        if day.date() >= today.date():
            break
    return result


def format_week_logs_for_prompt(week_logs: dict) -> str:
    if not week_logs:
        return "本周暂无工作记录。"
    lines = []
    for date, entries in sorted(week_logs.items()):
        lines.append(f"【{date}】")
        for e in entries:
            lines.append(f"  {e['time']} - {e['content']}")
    return "\n".join(lines)


_DOUBAO_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def _doubao_request(api_key: str, endpoint_id: str, messages: list,
                    max_tokens: int = 1000, api_url: str = "") -> str:
    import urllib.request
    import urllib.error
    import time
    url = api_url or _DOUBAO_URL
    payload = json.dumps({
        "model": endpoint_id,
        "max_tokens": max_tokens,
        "messages": messages
    }).encode("utf-8")

    for attempt in range(3):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(8 * (attempt + 1))   # 8s → 16s
                continue
            raise


def generate_daily_summary(api_key: str, endpoint_id: str, window_log: list,
                            manual_entries: list, context_text: str = "",
                            activity_entries: list = None, api_url: str = "") -> str:
    """
    根据今天的多路数据，让AI生成日报草稿。
    window_log:       [{"time": "10:05", "title": "..."}, {"time":..., "screenshot":...}]
    manual_entries:   [{"time": "14:30", "content": "完成了订单模块接口联调"}, ...]
    context_text:     ContextCollector.get_context_snapshot() 补充上下文，可为空。
    activity_entries: [{"time": "09:02", "summary": "调试佳易鑫人员定位系统"}, ...]
    返回纯文本，每行一条工作内容，方便用户在弹框里编辑。
    """
    now = datetime.now()

    # 活动摘要（最丰富的来源，优先放最上面）
    activity_text = "（无活动摘要）"
    if activity_entries:
        lines = [f"  {e['time']} - {e['summary']}" for e in activity_entries]
        activity_text = "\n".join(lines)

    window_text = "（无窗口记录）"
    if window_log:
        lines = []
        for e in window_log:
            if "screenshot" in e:
                lines.append(f"  {e['time']} [截图分析] {e['screenshot']}")
            elif "title" in e:
                lines.append(f"  {e['time']} [窗口标题] {e['title']}")
        window_text = "\n".join(lines) if lines else "（无窗口记录）"

    manual_text = "（无手动记录）"
    if manual_entries:
        lines = [f"  {e['time']} - {e['content']}" for e in manual_entries]
        manual_text = "\n".join(lines)

    context_block = f"\n【补充上下文（中文输入/截图描述）】\n{context_text}\n" if context_text else ""

    prompt = f"""今天是{now.strftime("%Y年%m月%d日")}，以下是用户今天的工作数据：

【用户手动记录（最权威，优先采用）】
{manual_text}

【AI活动摘要（每2分钟自动生成，作为补充参考）】
{activity_text}

【窗口标题记录（每5分钟采样，仅供参考）】
{window_text}
{context_block}
请根据以上信息，提取今天实际完成的工作，生成日报草稿。

【用户工作背景】
用户是一名系统工程师，当前主要负责三个项目：
1. 人员定位系统——为各企业客户（合理化工、静善生物、蓝洁、英达、同辉、佳易鑫、拓凯、中远海运等）部署、维护和问题处理
2. 烟花爆竹预警系统——开发、部署、维护（含生产企业和批发企业模块）
3. 市平台——数据推送、定时器、接口对接等开发维护工作
此外还包括服务器运维（清理/漏洞修复/病毒处理）、技术文档编写、客户培训、出差汇报等配套工作。

【严格过滤规则——以下内容绝对不写入日报】
- 锁屏、解锁、待机、电脑开关机等系统操作
- 与工作无关的即时通讯（微信/QQ 闲聊、刷朋友圈等）
- 游戏、游戏Wiki/攻略/论坛等娱乐内容
- 刷视频、看新闻、网购等个人行为
- 任何明显的非工作、非编程、非技术类活动

【输出要求】
- 每条工作一行，用"-"开头
- 优先采用「用户手动记录」的内容，AI活动摘要仅用于补充手动记录未覆盖的工作
- 格式：动词 + 项目/系统名 + 具体内容，语言简洁准确
- 合并同类项，去除重复，不逐条罗列
- 只输出工作条目，不加标题、日期、总结句、解释
- 最多8条；如果今天确实没有工作记录，只输出"（今日暂无工作记录）"

示例（好的格式）：
- 修复烟花爆竹预警系统喊话功能BUG
- 部署蓝洁、佳易鑫人员定位系统
- 处理同辉科盛服务器连接异常
- 前往广西应急厅汇报项目进度"""

    return _doubao_request(api_key, endpoint_id, [
        {"role": "user", "content": prompt}
    ], max_tokens=600, api_url=api_url)


def get_today_activity_summaries() -> list:
    """只读取高/中置信的工作摘要，娱乐与不确定片段不进入日报。"""
    activity_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_log.json")
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if not os.path.exists(activity_file):
            return []
        data = json.loads(open(activity_file, encoding="utf-8").read())
        if data.get("date") != today:
            return []
        entries = data.get("entries", [])
        result, prev = [], None
        for e in entries:
            s = e.get("summary", "").strip()
            # 旧数据没有分类，不能再把它当成可信工作事实，避免历史娱乐记录混入。
            kind = e.get("kind", "uncertain")
            confidence = e.get("confidence", "medium")
            if s and kind == "work" and confidence in ("high", "medium") and s != prev:
                result.append({"time": e.get("time", ""), "summary": s,
                               "evidence": e.get("evidence", [])})
                prev = s
        return result
    except Exception:
        return []


def _get_activity_logs_for_week() -> dict:
    """读取 activity_log.json，提取本周各天的活动摘要列表"""
    activity_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_log.json")
    result = {}
    try:
        if not os.path.exists(activity_file):
            return result
        data = json.loads(open(activity_file, encoding="utf-8").read())
        date_str = data.get("date", "")
        entries  = data.get("entries", [])
        if date_str and entries:
            # 只保留有内容的摘要，去重
            summaries = list(dict.fromkeys(
                e["summary"] for e in entries if e.get("summary", "").strip()
            ))
            if summaries:
                result[date_str] = summaries
    except Exception:
        pass
    return result


def generate_weekly_report(api_key: str, endpoint_id: str, api_url: str = "") -> str:
    """生成周报：优先用确认过的日报，没有日报的天用活动摘要兜底"""
    now      = datetime.now()
    monday   = now - timedelta(days=now.weekday())
    week_start = monday.strftime("%Y年%m月%d日")
    week_end   = now.strftime("%Y年%m月%d日")

    week_logs    = get_week_logs()           # 已确认的日报条目
    activity_logs = _get_activity_logs_for_week()  # 活动摘要（兜底）

    # 按天构建原始数据块
    day_blocks = []
    for i in range(now.weekday() + 1):      # 周一到今天
        day = monday + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        label = f"【{key} 周{['一','二','三','四','五','六','日'][day.weekday()]}】"

        if key in week_logs and week_logs[key]:
            # 有确认日报 → 直接用
            lines = [f"  {e['time']} {e['content']}" for e in week_logs[key]]
            day_blocks.append(label + "\n" + "\n".join(lines))
        elif key in activity_logs:
            # 无日报，有活动摘要 → 作为补充参考
            lines = ["  " + s for s in activity_logs[key]]
            day_blocks.append(label + "（来自活动记录，供参考）\n" + "\n".join(lines))
        else:
            day_blocks.append(label + "\n  （无记录）")

    raw_data = "\n\n".join(day_blocks)

    prompt = f"""以下是我本周每天的工作数据（周一到今天）：

{raw_data}

【我的工作背景】
系统工程师，当前主要负责三个项目：人员定位系统（多客户部署/维护）、烟花爆竹预警系统（开发/部署/维护）、市平台（数据推送/接口开发）；以及服务器运维、文档编写、客户培训、出差汇报等配套工作。

【过滤规则——以下内容绝对不写入周报】
锁屏/解锁/待机、与工作无关的即时通讯、游戏/娱乐内容、刷视频/购物等个人行为。

请根据以上数据生成周报，格式要求：

{WEEKLY_REPORT_FORMAT}

本周时间范围：{week_start} - {week_end}
今天是{now.strftime("%Y年%m月%d日")}，星期{['一','二','三','四','五','六','日'][now.weekday()]}。
注意：标注「来自活动记录」的内容是自动采集的，请严格过滤非工作内容，合理归纳，不要原样照抄。
只输出周报正文，不要加任何解释。"""

    return _doubao_request(api_key, endpoint_id, [
        {"role": "user", "content": prompt}
    ], max_tokens=1200, api_url=api_url)


def understand_intent(api_key: str, endpoint_id: str, user_input: str, api_url: str = "") -> dict:
    now = datetime.now()
    today_count = len(get_week_logs().get(now.strftime("%Y-%m-%d"), []))

    system_prompt = f"""你是一个桌面宠物助手，帮用户记录工作和生成周报。
今天是{now.strftime("%Y年%m月%d日")}，今天已有{today_count}条工作记录。

根据用户输入判断意图，只返回JSON，格式：
- 记录工作内容：{{"intent": "record", "content": "提炼后的工作内容"}}
- 生成/发送周报：{{"intent": "generate_report", "content": ""}}
- 打开钉钉：{{"intent": "open_dingtalk", "content": ""}}
- 其他对话：{{"intent": "chat", "content": "简短回复，不超过30字，口语化，可爱一点"}}

只返回JSON，不要任何解释。"""

    text = _doubao_request(api_key, endpoint_id, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ], max_tokens=200, api_url=api_url)

    text = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)
