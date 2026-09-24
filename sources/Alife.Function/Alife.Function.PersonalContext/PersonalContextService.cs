using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Alife.Framework;
using Alife.Function.DeskPet;
using Alife.Function.FunctionCaller;
using Alife.Function.MessageFilter;

namespace Alife.Function.PersonalContext;

[Module("个人情境",
    "将个人档案、今日工作记录及屏幕活动注入 AI 上下文，让 AI 了解你的工作状态与个人情况。同时支持 AI 主动记录和查询工作日志。",
    defaultCategory: "个人定制")]
public class PersonalContextService(
    XmlFunctionCaller functionCaller,
    MessageFilterService messageFilterService,
    Interactor<PersonalContextService> interactor,
    IDeskPet? deskPet = null) :
    ChatBehaviour,
    IConfigurable<PersonalContextConfig>
{
    public PersonalContextConfig Configuration { get; set; } = null!;

    WorkingPetReader? reader;

    protected override Task OnAwake()
    {
        if (!string.IsNullOrEmpty(Configuration.WorkingPetDataPath))
            reader = new WorkingPetReader(Configuration.WorkingPetDataPath);

        // 注入个人档案到系统提示词
        if (!string.IsNullOrEmpty(Configuration.PersonalProfile))
        {
            interactor.Prompt($"""
                               以下是用户的个人档案，请充分了解并记住，以便在对话中更好地理解和回应用户：
                               {Configuration.PersonalProfile}
                               """);
        }

        // 注册工作日志 XML 函数
        XmlHandler handler = new(this) {
            Description = "用于记录用户完成的工作事项，查询工作日志。当用户提到完成了某项工作、完成某个任务时，应主动调用 worklog-add 进行记录。"
        };
        functionCaller.RegisterHandler(handler, cancellationToken: DestroyCancellationToken);

        // 周期性注入当前情境摘要（跟随 MessageFilter 的 InjectionInterval 节奏）
        messageFilterService.AddMessageReplyGuidance(BuildContextGuidance, DestroyCancellationToken);

        // 启动下班提醒监控
        if (!string.IsNullOrEmpty(Configuration.WorkSchedule))
            _ = OffWorkMonitorLoop();

        return Task.CompletedTask;
    }

    // 添加工作日志
    [XmlFunction(FunctionMode.OneShot, "worklog-add")]
    [Description("记录一条工作日志。用户表示完成了某项工作任务时调用。")]
    public void AddWorkLog([Description("工作内容的简短描述")] string content)
    {
        if (reader == null)
        {
            interactor.Poke("未配置WorkingPet数据目录，无法记录工作日志");
            return;
        }

        var log = reader.ReadWorkLog();
        string today = DateTime.Now.ToString("yyyy-MM-dd");
        if (!log.ContainsKey(today))
            log[today] = [];

        log[today].Add(new WorkLogEntry {
            Time = DateTime.Now.ToString("HH:mm"),
            Content = content.Trim()
        });
        reader.WriteWorkLog(log);

        int count = log[today].Count;
        interactor.Poke($"[工作日志] 今日第{count}条已记录：{content}");
    }

    // 查询工作日志
    [XmlFunction(FunctionMode.OneShot, "worklog-query")]
    [Description("查询指定日期的工作日志，不传日期则查今天")]
    public void QueryWorkLog([Description("日期，格式 yyyy-MM-dd，不填默认今天")] string date = "")
    {
        if (reader == null)
        {
            interactor.Poke("未配置WorkingPet数据目录");
            return;
        }

        if (string.IsNullOrEmpty(date))
            date = DateTime.Now.ToString("yyyy-MM-dd");

        var log = reader.ReadWorkLog();
        if (!log.TryGetValue(date, out var entries) || entries.Count == 0)
        {
            interactor.Poke($"{date} 暂无工作记录");
            return;
        }

        var sb = new StringBuilder();
        sb.AppendLine($"{date} 工作记录（共 {entries.Count} 条）：");
        foreach (var e in entries)
            sb.AppendLine($"  {e.Time}  {e.Content}");
        interactor.Poke(sb.ToString().TrimEnd());
    }

    // 查询近N天工作汇总
    [XmlFunction(FunctionMode.OneShot, "worklog-summary")]
    [Description("汇总近N天的工作记录，用于生成日报或周报")]
    public void WorkLogSummary([Description("查询最近几天，默认7天")] int days = 7)
    {
        if (reader == null)
        {
            interactor.Poke("未配置WorkingPet数据目录");
            return;
        }

        var log = reader.ReadWorkLog();
        var sb = new StringBuilder();
        sb.AppendLine($"最近 {days} 天工作记录：");

        bool hasAny = false;
        for (int i = days - 1; i >= 0; i--)
        {
            string date = DateTime.Now.AddDays(-i).ToString("yyyy-MM-dd");
            if (!log.TryGetValue(date, out var entries) || entries.Count == 0)
                continue;

            hasAny = true;
            sb.AppendLine($"\n{date}（{entries.Count} 条）：");
            foreach (var e in entries)
                sb.AppendLine($"  {e.Time}  {e.Content}");
        }

        if (!hasAny)
            sb.AppendLine("（无记录）");

        interactor.Poke(sb.ToString().TrimEnd());
    }

    string BuildContextGuidance()
    {
        if (reader == null)
            return "";

        var sb = new StringBuilder();
        bool hasContent = false;

        // 今日工作记录
        if (Configuration.InjectWorkLog)
        {
            var workLog = reader.ReadWorkLog();
            string today = DateTime.Now.ToString("yyyy-MM-dd");
            if (workLog.TryGetValue(today, out var todayEntries) && todayEntries.Count > 0)
            {
                sb.AppendLine($"今日工作记录（{todayEntries.Count} 条）：");
                foreach (var e in todayEntries)
                    sb.AppendLine($"  {e.Time}  {e.Content}");
                hasContent = true;
            }
        }

        // 最近屏幕活动
        if (Configuration.InjectScreenContext)
        {
            var contextLog = reader.ReadContextLog();
            var recentScreenshots = contextLog.Screenshots
                .TakeLast(Configuration.ScreenContextCount)
                .ToList();
            if (recentScreenshots.Count > 0)
            {
                sb.AppendLine("最近屏幕活动：");
                foreach (var s in recentScreenshots)
                    sb.AppendLine($"  {s.Time}  {s.Text}");
                hasContent = true;
            }
        }

        if (!hasContent)
            return "";

        return $"[用户当前情境]\n{sb.ToString().TrimEnd()}";
    }

    async Task OffWorkMonitorLoop()
    {
        bool wasWorkTime = IsWorkTime();
        using PeriodicTimer timer = new(TimeSpan.FromMinutes(1));
        try
        {
            while (await timer.WaitForNextTickAsync(DestroyCancellationToken))
            {
                bool isWork = IsWorkTime();
                if (wasWorkTime && !isWork)
                    await TriggerOffWorkReminder();
                wasWorkTime = isWork;
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e) { AlifeLog.LogError(e); }
    }

    async Task TriggerOffWorkReminder()
    {
        interactor.Poke("下班时间到了！今天辛苦了，记得好好休息 🎉");
        if (deskPet == null) return;
        try
        {
            await deskPet.Resize(480, 720);
            await deskPet.MoveToCenter();
        }
        catch (Exception e) { AlifeLog.LogWarning(e); }
    }

    // 判断当前是否在工作时间
    bool IsWorkTime()
    {
        if (string.IsNullOrEmpty(Configuration.WorkSchedule))
            return false;

        TimeSpan now = DateTime.Now.TimeOfDay;
        foreach (string segment in Configuration.WorkSchedule.Split(',', StringSplitOptions.RemoveEmptyEntries))
        {
            string[] parts = segment.Trim().Split('-');
            if (parts.Length != 2) continue;
            if (TimeSpan.TryParse(parts[0].Trim(), out var start) &&
                TimeSpan.TryParse(parts[1].Trim(), out var end))
            {
                if (now >= start && now <= end)
                    return true;
            }
        }
        return false;
    }
}
