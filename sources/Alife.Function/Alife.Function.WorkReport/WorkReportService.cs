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

    DateTime lastDailyReportDate = DateTime.MinValue;
    DateTime lastWeeklyReportDate = DateTime.MinValue;

    protected override Task OnAwake()
    {
        XmlHandler xmlHandler = new(this) {
            Description = "工作日志管理工具。用于记录工作内容、查询今日工作、生成日报和周报。",
            Explanation = """
                          ## 使用说明
                          - 当用户提到做了什么工作时，主动调用 RecordWork 记录
                          - 用户要求查看今天工作时，调用 GetTodayLog
                          - 用户要求生成日报或周报时，调用对应方法
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
            // 将 config 中 0=周一,4=周五 映射到本周对应日期
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

        string prompt = $"请根据以下本周工作数据生成周报（{weekStart} - {weekEnd}）：\n\n{dataBlock.ToString().TrimEnd()}\n\n周报格式：\n标题：周报（{weekStart} - {weekEnd}）\n开头：本周共完成X项工作，主要涉及[不超过4个关键词]等方面。\n分项用\"-\"开头，格式：[小结词]：[内容]\n语言平实，不加修饰词。下周计划若无则写"暂无具体安排"。\n只输出周报正文，不加任何解释。";

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
