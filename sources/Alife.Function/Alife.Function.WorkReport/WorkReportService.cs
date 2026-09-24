using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Alife.Foundation;
using Alife.Framework;
using Alife.Function.FunctionCaller;
using ElectronNET.API;
using ElectronNET.API.Entities;

namespace Alife.Function.WorkReport;

[Module("工作日报",
    "记录每日工作内容，在下班时自动生成日报，并支持生成周报。",
    defaultCategory: "个人定制")]
public class WorkReportService(
    XmlFunctionCaller functionCaller,
    Interactor<WorkReportService> interactor) :
    ChatBehaviour,
    IConfigurable<WorkReportConfig>
{
    public WorkReportConfig Configuration { get; set; } = null!;

    BrowserWindow? calendarWindow;

    [XmlFunction(FunctionMode.OneShot)]
    [Description("记录一条工作内容到今日工作日志")]
    public string RecordWork(string content)
    {
        content = content.Trim();
        if (string.IsNullOrEmpty(content))
            return "内容不能为空。";

        string time = DateTime.Now.ToString("HH:mm");
        string today = DateTime.Now.ToString("yyyy-MM-dd");

        Dictionary<string, List<WorkEntry>> logs = LoadLogs();
        if (!logs.ContainsKey(today))
            logs[today] = new List<WorkEntry>();
        logs[today].Add(new WorkEntry { Time = time, Content = content });
        SaveLogs(logs);

        return $"已记录，今日共 {logs[today].Count} 条工作记录。";
    }

    [XmlFunction(FunctionMode.OneShot)]
    [Description("查看今日工作记录")]
    public string GetTodayLog()
    {
        List<WorkEntry> entries = GetTodayEntries();
        if (entries.Count == 0)
            return "今日暂无工作记录。";

        StringBuilder sb = new();
        foreach (WorkEntry e in entries)
            sb.AppendLine($"{e.Time} - {e.Content}");
        return sb.ToString().TrimEnd();
    }

    [XmlFunction(FunctionMode.OneShot)]
    [Description("生成今日日报（将引导 AI 根据今日工作记录整理日报）")]
    public string GenerateDailyReport()
    {
        TriggerDailyReport();
        return "已发起日报生成请求。";
    }

    [XmlFunction(FunctionMode.OneShot)]
    [Description("生成本周周报（将引导 AI 根据本周工作记录整理周报）")]
    public string GenerateWeeklyReport()
    {
        TriggerWeeklyReport();
        return "已发起周报生成请求。";
    }

    [XmlFunction(FunctionMode.OneShot)]
    [Description("打开工作日历窗口，可浏览任意日期的工作记录")]
    public string ShowWorkCalendar()
    {
        OpenCalendarWindow();
        return "工作日历已打开。";
    }

    DateTime lastDailyReportDate = DateTime.MinValue;
    DateTime lastWeeklyReportDate = DateTime.MinValue;

    protected override Task OnAwake()
    {
        XmlHandler xmlHandler = new(this) {
            Description = "工作日志管理工具。用于记录工作内容、查询今日工作、生成日报和周报、查看工作日历。",
            Explanation = """
                          ## 使用说明
                          - 当用户提到做了什么工作时，主动调用 RecordWork 记录
                          - 用户要求查看今天工作时，调用 GetTodayLog
                          - 用户要求生成日报或周报时，调用对应方法
                          - 用户要求查看工作日历或历史记录时，调用 ShowWorkCalendar
                          - 系统会在配置的下班时间自动触发日报生成
                          """
        };
        functionCaller.RegisterHandler(xmlHandler, cancellationToken: DestroyCancellationToken);

        string? dir = Path.GetDirectoryName(LogFilePath);
        if (dir != null)
            Directory.CreateDirectory(dir);

        return Task.CompletedTask;
    }

    protected override Task OnUpdate()
    {
        DateTime now = DateTime.Now;
        DateTime today = now.Date;

        if (Configuration.AutoDailyReport)
        {
            TimeSpan triggerTime = ParseTime(Configuration.DailyReportTime);
            if (lastDailyReportDate < today && now.TimeOfDay >= triggerTime)
            {
                lastDailyReportDate = today;
                TriggerDailyReport();
            }
        }

        if (Configuration.AutoWeeklyReport)
        {
            DateTime monday = today.AddDays(-(((int)today.DayOfWeek + 6) % 7));
            DateTime thisWeekTriggerDay = monday.AddDays(Configuration.WeeklyReportDayOfWeek);
            TimeSpan weeklyTriggerTime = ParseTime(Configuration.WeeklyReportTime);

            if (lastWeeklyReportDate < thisWeekTriggerDay &&
                today >= thisWeekTriggerDay &&
                now.TimeOfDay >= weeklyTriggerTime)
            {
                lastWeeklyReportDate = thisWeekTriggerDay;
                TriggerWeeklyReport();
            }
        }

        return Task.CompletedTask;
    }

    protected override Task OnDestroy()
    {
        try { calendarWindow?.Destroy(); } catch { }
        calendarWindow = null;
        return Task.CompletedTask;
    }

    async void OpenCalendarWindow()
    {
        try
        {
            if (calendarWindow != null)
            {
                try { calendarWindow.Destroy(); } catch { }
                calendarWindow = null;
            }

            string dir = Path.Combine(AlifePath.RuntimeFolderPath, "WorkReport");
            Directory.CreateDirectory(dir);
            string htmlPath = Path.Combine(dir, "calendar.html");

            Dictionary<string, List<WorkEntry>> logs = LoadLogs();
            File.WriteAllText(htmlPath, BuildCalendarHtml(logs), Encoding.UTF8);

            string url = new Uri(htmlPath).AbsoluteUri;
            calendarWindow = await Electron.WindowManager.CreateWindowAsync(new BrowserWindowOptions {
                Title = "工作日历",
                Width = 720,
                Height = 500,
                Frame = false,
                Transparent = false,
                HasShadow = true,
                Show = false,
                Resizable = false,
                Fullscreenable = false,
                WebPreferences = new WebPreferences {
                    NodeIntegration = true,
                    ContextIsolation = false,
                    Sandbox = false,
                }
            }, url);

            calendarWindow.OnClosed += () => calendarWindow = null;

            TaskCompletionSource tcs = new();
            calendarWindow.OnReadyToShow += () => tcs.TrySetResult();
            await Task.WhenAny(tcs.Task, Task.Delay(5000));
            calendarWindow.Show();
        }
        catch (Exception e)
        {
            AlifeLog.LogError(e);
        }
    }

    void TriggerDailyReport()
    {
        string date = DateTime.Now.ToString("yyyy年MM月dd日");
        List<WorkEntry> entries = GetTodayEntries();

        string logsText;
        if (entries.Count == 0)
        {
            logsText = "（今日暂无工作记录）";
        }
        else
        {
            StringBuilder sb = new();
            foreach (WorkEntry e in entries)
                sb.AppendLine($"  {e.Time} {e.Content}");
            logsText = sb.ToString().TrimEnd();
        }

        string prompt = $"现在是 {date} 下班时间，请根据以下今日工作记录整理日报：\n\n{logsText}\n\n日报格式要求：\n- 每条工作用\"-\"开头，格式：动词 + 具体内容，语言简洁准确\n- 合并同类项，去除重复，最多8条\n- 如无工作记录，只输出\"（今日暂无工作记录）\"\n- 只输出工作条目，不加标题、日期说明或其他解释";

        interactor.Poke(prompt);
    }

    void TriggerWeeklyReport()
    {
        DateTime now = DateTime.Now;
        DateTime monday = now.Date.AddDays(-(((int)now.DayOfWeek + 6) % 7));
        string weekStart = monday.ToString("yyyy年MM月dd日");
        string weekEnd = now.ToString("yyyy年MM月dd日");

        Dictionary<string, List<WorkEntry>> allLogs = LoadLogs();
        StringBuilder dataBlock = new();
        string[] weekDayNames = { "一", "二", "三", "四", "五", "六", "日" };

        int dayCount = (int)(now.Date - monday).TotalDays + 1;
        for (int i = 0; i < dayCount; i++)
        {
            DateTime day = monday.AddDays(i);
            string key = day.ToString("yyyy-MM-dd");
            int weekDayIndex = ((int)day.DayOfWeek + 6) % 7;
            dataBlock.AppendLine($"【{key} 周{weekDayNames[weekDayIndex]}】");

            if (allLogs.ContainsKey(key) && allLogs[key].Count > 0)
            {
                foreach (WorkEntry e in allLogs[key])
                    dataBlock.AppendLine($"  {e.Time} {e.Content}");
            }
            else
            {
                dataBlock.AppendLine("  （无记录）");
            }
        }

        string prompt = $"请根据以下本周工作数据生成周报（{weekStart} - {weekEnd}）：\n\n{dataBlock.ToString().TrimEnd()}\n\n周报格式：\n标题：周报（{weekStart} - {weekEnd}）\n开头：本周共完成X项工作，主要涉及[不超过4个关键词]等方面。\n分项用\"-\"开头，格式：[小结词]：[内容]\n语言平实，不加修饰词。下周计划若无则写\"暂无具体安排\"。\n只输出周报正文，不加任何解释。";

        interactor.Poke(prompt);
    }

    List<WorkEntry> GetTodayEntries()
    {
        string today = DateTime.Now.ToString("yyyy-MM-dd");
        Dictionary<string, List<WorkEntry>> logs = LoadLogs();
        return logs.ContainsKey(today) ? logs[today] : new List<WorkEntry>();
    }

    Dictionary<string, List<WorkEntry>> LoadLogs()
    {
        try
        {
            if (!File.Exists(LogFilePath))
                return new Dictionary<string, List<WorkEntry>>();
            string json = File.ReadAllText(LogFilePath, Encoding.UTF8);
            return JsonSerializer.Deserialize<Dictionary<string, List<WorkEntry>>>(json, JsonOptions)
                   ?? new Dictionary<string, List<WorkEntry>>();
        }
        catch
        {
            return new Dictionary<string, List<WorkEntry>>();
        }
    }

    void SaveLogs(Dictionary<string, List<WorkEntry>> logs)
    {
        string json = JsonSerializer.Serialize(logs, JsonOptions);
        File.WriteAllText(LogFilePath, json, Encoding.UTF8);
    }

    static string BuildCalendarHtml(Dictionary<string, List<WorkEntry>> logs)
    {
        string logsJson = JsonSerializer.Serialize(logs, JsonOptions);
        const string template = """
            <!DOCTYPE html>
            <html lang="zh-CN">
            <head>
            <meta charset="UTF-8">
            <style>
              * { box-sizing: border-box; margin: 0; padding: 0; }
              body {
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                background: #1a1a2e;
                color: #e0e0e0;
                height: 100vh;
                display: flex;
                flex-direction: column;
                overflow: hidden;
                user-select: none;
              }
              .titlebar {
                background: #16213e;
                padding: 8px 16px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                border-bottom: 1px solid #0f3460;
                -webkit-app-region: drag;
                flex-shrink: 0;
              }
              .titlebar h1 { font-size: 14px; font-weight: 600; color: #a0c4ff; }
              .close-btn {
                width: 20px; height: 20px; border-radius: 50%;
                background: rgba(231,76,60,0.7); border: none; cursor: pointer;
                display: flex; align-items: center; justify-content: center;
                color: white; font-size: 11px; -webkit-app-region: no-drag;
                transition: background 0.2s;
              }
              .close-btn:hover { background: #e74c3c; }
              .content {
                display: flex;
                flex: 1;
                overflow: hidden;
              }
              .calendar-panel {
                width: 310px;
                padding: 16px;
                border-right: 1px solid #0f3460;
                display: flex;
                flex-direction: column;
                flex-shrink: 0;
              }
              .cal-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 12px;
              }
              .cal-header h2 { font-size: 14px; color: #a0c4ff; font-weight: 600; }
              .nav-btn {
                background: none; border: none; color: #a0c4ff;
                cursor: pointer; font-size: 18px; padding: 0 6px;
                line-height: 1;
              }
              .nav-btn:hover { color: #fff; }
              .cal-grid {
                display: grid;
                grid-template-columns: repeat(7, 1fr);
                gap: 2px;
              }
              .cal-weekday {
                text-align: center; font-size: 11px; color: #556;
                padding: 4px 0;
              }
              .cal-day {
                text-align: center;
                font-size: 12px; border-radius: 6px;
                cursor: pointer; position: relative;
                height: 34px; display: flex;
                flex-direction: column; align-items: center; justify-content: center;
                gap: 2px;
                transition: background 0.15s;
              }
              .cal-day:not(.empty):hover { background: #1e3a5f; }
              .cal-day.today { background: #0f3460; color: #a0c4ff; font-weight: bold; }
              .cal-day.selected { background: #1a4f80; color: #fff; }
              .cal-day.has-record::after {
                content: "";
                display: block;
                width: 4px; height: 4px;
                background: #4fc3f7;
                border-radius: 50%;
              }
              .cal-day.empty { cursor: default; }
              .log-panel {
                flex: 1;
                padding: 16px;
                display: flex;
                flex-direction: column;
                overflow: hidden;
              }
              .log-header {
                font-size: 13px; color: #a0c4ff;
                margin-bottom: 12px; padding-bottom: 8px;
                border-bottom: 1px solid #0f3460;
                display: flex; align-items: center; justify-content: space-between;
                flex-shrink: 0;
              }
              .count-badge {
                background: #1a4080; color: #7eb8f0;
                font-size: 11px; padding: 2px 8px; border-radius: 10px;
              }
              .log-list {
                flex: 1;
                overflow-y: auto;
              }
              .log-list::-webkit-scrollbar { width: 4px; }
              .log-list::-webkit-scrollbar-track { background: transparent; }
              .log-list::-webkit-scrollbar-thumb { background: #1a3a5c; border-radius: 4px; }
              .log-entry {
                display: flex;
                gap: 10px;
                padding: 9px 0;
                border-bottom: 1px solid #0f2040;
                font-size: 13px;
                line-height: 1.5;
              }
              .log-entry:last-child { border-bottom: none; }
              .log-time { color: #4fc3f7; min-width: 42px; font-size: 12px; flex-shrink: 0; }
              .log-content { color: #d0d0d0; word-break: break-all; }
              .empty-tip { color: #445; font-size: 13px; margin-top: 30px; text-align: center; }
            </style>
            </head>
            <body>
            <div class="titlebar">
              <h1>&#128197; 工作日历</h1>
              <button class="close-btn" onclick="window.close()">&#10005;</button>
            </div>
            <div class="content">
              <div class="calendar-panel">
                <div class="cal-header">
                  <button class="nav-btn" onclick="prevMonth()">&#8249;</button>
                  <h2 id="month-title"></h2>
                  <button class="nav-btn" onclick="nextMonth()">&#8250;</button>
                </div>
                <div class="cal-grid" id="cal-grid">
                  <div class="cal-weekday">日</div>
                  <div class="cal-weekday">一</div>
                  <div class="cal-weekday">二</div>
                  <div class="cal-weekday">三</div>
                  <div class="cal-weekday">四</div>
                  <div class="cal-weekday">五</div>
                  <div class="cal-weekday">六</div>
                </div>
              </div>
              <div class="log-panel">
                <div class="log-header">
                  <span id="log-date">选择日期查看记录</span>
                  <span class="count-badge" id="log-count"></span>
                </div>
                <div class="log-list" id="log-list">
                  <div class="empty-tip">&#8592; 点击左侧日历查看工作记录</div>
                </div>
              </div>
            </div>
            <script>
              var logs = __LOGS_JSON__;
              var today = new Date();
              var curYear = today.getFullYear();
              var curMonth = today.getMonth();
              var selectedDate = null;

              function dateKey(y, m, d) {
                return y + '-' + String(m+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
              }

              function renderCalendar() {
                document.getElementById('month-title').textContent =
                  curYear + '年' + (curMonth+1) + '月';
                var grid = document.getElementById('cal-grid');
                while (grid.children.length > 7) grid.removeChild(grid.lastChild);
                var firstDay = new Date(curYear, curMonth, 1).getDay();
                var daysInMonth = new Date(curYear, curMonth+1, 0).getDate();
                for (var i = 0; i < firstDay; i++) {
                  var empty = document.createElement('div');
                  empty.className = 'cal-day empty';
                  grid.appendChild(empty);
                }
                for (var d = 1; d <= daysInMonth; d++) {
                  var key = dateKey(curYear, curMonth, d);
                  var cell = document.createElement('div');
                  var cls = 'cal-day';
                  var isToday = curYear === today.getFullYear() && curMonth === today.getMonth() && d === today.getDate();
                  if (isToday) cls += ' today';
                  if (selectedDate === key) cls += ' selected';
                  if (logs[key] && logs[key].length > 0) cls += ' has-record';
                  cell.className = cls;
                  cell.textContent = d;
                  (function(k, y, m, day) {
                    cell.onclick = function() { selectDay(k, y, m, day); };
                  })(key, curYear, curMonth, d);
                  grid.appendChild(cell);
                }
              }

              function selectDay(key, y, m, d) {
                selectedDate = key;
                renderCalendar();
                var entries = logs[key] || [];
                document.getElementById('log-date').textContent = y + '年' + (m+1) + '月' + d + '日';
                document.getElementById('log-count').textContent = entries.length > 0 ? entries.length + ' 条记录' : '';
                var logList = document.getElementById('log-list');
                if (entries.length === 0) {
                  logList.innerHTML = '<div class="empty-tip">当日无工作记录</div>';
                } else {
                  logList.innerHTML = entries.map(function(e) {
                    return '<div class="log-entry"><span class="log-time">' + e.time + '</span><span class="log-content">' + e.content + '</span></div>';
                  }).join('');
                }
              }

              function prevMonth() { curMonth--; if(curMonth<0){curMonth=11;curYear--;} renderCalendar(); }
              function nextMonth() { curMonth++; if(curMonth>11){curMonth=0;curYear++;} renderCalendar(); }

              renderCalendar();
              var todayKey = dateKey(today.getFullYear(), today.getMonth(), today.getDate());
              selectDay(todayKey, today.getFullYear(), today.getMonth(), today.getDate());
            </script>
            </body>
            </html>
            """;
        return template.Replace("__LOGS_JSON__", logsJson);
    }

    static TimeSpan ParseTime(string timeStr)
    {
        try
        {
            string[] parts = timeStr.Split(':');
            return new TimeSpan(int.Parse(parts[0]), int.Parse(parts[1]), 0);
        }
        catch
        {
            return new TimeSpan(18, 0, 0);
        }
    }

    string LogFilePath => Path.Combine(AlifePath.StorageFolderPath, "WorkReport", "work_log.json");

    static readonly JsonSerializerOptions JsonOptions = new() {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };
}

class WorkEntry
{
    public string Time { get; set; } = "";
    public string Content { get; set; } = "";
}
