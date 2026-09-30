using System;
using System.ComponentModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Alife.Foundation;
using Alife.Framework;
using Alife.Function.AIModelUtility;
using Alife.Function.FunctionCaller;

namespace Alife.Function.BehaviorCollector;

[Module("行为采集",
    "在后台采集应用使用时长、鼠标活跃度、中文输入、屏幕文字、浏览历史和屏幕描述，供日报、周报和个人情境使用。所有数据只保存在本机。",
    defaultCategory: "个人定制")]
public class BehaviorCollectorService(
    XmlFunctionCaller functionCaller,
    IVisionModel? visionModel = null) :
    ChatBehaviour,
    IConfigurable<BehaviorCollectorConfig>
{
    public BehaviorCollectorConfig Configuration { get; set; } = null!;

    /// <summary>识别出一段工作活动时触发，参数为摘要文本</summary>
    public event Action<string>? WorkDetected;

    /// <summary>当前识别出的工作摘要，没有时为空</summary>
    public string CurrentActivity => activityMonitor?.CurrentSummary ?? "";

    [XmlFunction(FunctionMode.OneShot)]
    [Description("查询用户今天的电脑行为概况：应用使用时长、鼠标活跃度、最近窗口、浏览记录、中文输入、屏幕文字与描述")]
    public string QueryBehavior()
    {
        string today = DateTime.Now.ToString("yyyy-MM-dd");
        StringBuilder sb = new();

        DayBehavior day = store.Read<System.Collections.Generic.Dictionary<string, DayBehavior>>(BehaviorStore.RawBehaviorFile)
            .GetValueOrDefault(today) ?? new DayBehavior();
        if (day.Apps.Count > 0)
        {
            sb.AppendLine("应用使用时长（前8）：");
            foreach ((string app, int seconds) in day.Apps.OrderByDescending(pair => pair.Value).Take(8))
                sb.AppendLine($"  {app}  {Math.Max(1, seconds / 60)} 分钟");
        }
        if (day.Mouse.Count > 0)
            sb.AppendLine($"鼠标活跃度：累计移动 {day.Mouse.Sum(m => (long)m.MoveDistance)} 像素，点击 {day.Mouse.Sum(m => m.Clicks)} 次");
        if (day.WindowTitles.Count > 0)
            sb.AppendLine($"最近窗口：{string.Join(" | ", day.WindowTitles.TakeLast(10))}");

        DayEntries<ActivityEntry> activity = store.Read<DayEntries<ActivityEntry>>(BehaviorStore.ActivityLogFile);
        if (activity.Date == today)
        {
            string[] works = activity.Entries.Where(e => e.Kind == "work" && e.Summary.Length > 0)
                .Select(e => $"{e.Time} {e.Summary}").TakeLast(8).ToArray();
            if (works.Length > 0)
                sb.AppendLine($"识别到的工作活动：{string.Join("；", works)}");
        }

        DayEntries<BrowserEntry> browser = store.Read<DayEntries<BrowserEntry>>(BehaviorStore.BrowserFile);
        if (browser.Date == today && browser.Entries.Count > 0)
            sb.AppendLine($"浏览记录：{string.Join(" | ", browser.Entries.Where(e => e.Title.Length > 0).Select(e => e.Title).TakeLast(10))}");

        string[] inputs = clipboardInput?.GetRecent(5) ?? [];
        if (inputs.Length > 0)
            sb.AppendLine($"最近中文输入：{string.Join(" / ", inputs)}");

        DayEntries<TextEntry> ocr = store.Read<DayEntries<TextEntry>>(BehaviorStore.OcrLogFile);
        if (ocr.Date == today && ocr.Entries.Count > 0)
        {
            TextEntry last = ocr.Entries[^1];
            sb.AppendLine($"最近一次屏幕文字（{last.Time}）：{(last.Text.Length > 300 ? last.Text[..300] + "…" : last.Text)}");
        }

        ContextLog context = store.Read<ContextLog>(BehaviorStore.ContextLogFile);
        if (context.Screenshots.Count > 0)
            sb.AppendLine($"屏幕描述：{string.Join("；", context.Screenshots.TakeLast(3).Select(s => $"{s.Time} {s.Text}"))}");

        return sb.Length == 0 ? "暂无采集数据。" : sb.ToString().TrimEnd();
    }

    BehaviorStore store = null!;
    ActivityMonitor? activityMonitor;
    ClipboardInputCollector? clipboardInput;
    string[] ignoredKeywords = [];

    protected override Task OnAwake()
    {
        string dataPath = string.IsNullOrWhiteSpace(Configuration.DataPath)
            ? System.IO.Path.Combine(AlifePath.StorageFolderPath, "WorkingPet")
            : Configuration.DataPath;
        store = new BehaviorStore(dataPath);
        ignoredKeywords = Configuration.IgnoredTitleKeywords
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

        XmlHandler handler = new(this) {
            Description = "查询用户在电脑上的行为概况，用于了解用户今天在做什么、写日报周报时补充素材。",
            Explanation = "所有数据只来自本机后台采集。用户问「我今天都干了什么」或需要日报素材时调用 QueryBehavior。"
        };
        functionCaller.RegisterHandler(handler, cancellationToken: DestroyCancellationToken);

        if (Configuration.EnableAppUsage)
        {
            AppUsageCollector collector = new(store, Configuration, IsIgnoredTitle);
            _ = CollectorLoop.Run(TimeSpan.Zero, () => TimeSpan.FromSeconds(Math.Max(1, Configuration.AppUsageIntervalSeconds)),
                collector.TickAsync, DestroyCancellationToken);
        }

        if (Configuration.EnableMouse)
            _ = Task.Run(RunMouseCollector);

        if (Configuration.EnableClipboardInput)
        {
            clipboardInput = new ClipboardInputCollector(store);
            _ = CollectorLoop.Run(TimeSpan.Zero, () => TimeSpan.FromSeconds(1), clipboardInput.TickAsync, DestroyCancellationToken);
        }

        if (Configuration.EnableOcr)
        {
            OcrCollector collector = new(store);
            // 启动后先等 1 分钟，避开冷启动的高负载
            _ = CollectorLoop.Run(TimeSpan.FromMinutes(1), () => TimeSpan.FromMinutes(Math.Max(1, Configuration.OcrIntervalMinutes)),
                collector.TickAsync, DestroyCancellationToken);
        }

        if (Configuration.EnableBrowserHistory)
        {
            BrowserHistoryCollector collector = new(store, Configuration);
            _ = CollectorLoop.Run(TimeSpan.FromMinutes(5), () => TimeSpan.FromMinutes(Math.Max(1, Configuration.BrowserIntervalMinutes)),
                collector.TickAsync, DestroyCancellationToken);
        }

        if (Configuration.EnableScreenDescription && visionModel != null)
        {
            ScreenDescriptionCollector collector = new(store, visionModel);
            // 启动后先等 5 分钟，之后每隔随机 15~30 分钟一次
            _ = CollectorLoop.Run(TimeSpan.FromMinutes(5), NextScreenDescriptionInterval, collector.TickAsync, DestroyCancellationToken);
        }

        if (Configuration.EnableActivityMonitor)
        {
            activityMonitor = new ActivityMonitor(store, IsIgnoredTitle);
            activityMonitor.SummaryChanged += summary => WorkDetected?.Invoke(summary);
            _ = CollectorLoop.Run(TimeSpan.Zero, () => ActivityMonitor.SampleInterval, activityMonitor.TickAsync, DestroyCancellationToken);
        }

        return Task.CompletedTask;
    }

    async Task RunMouseCollector()
    {
        try
        {
            await new MouseCollector(store).RunAsync(DestroyCancellationToken);
        }
        catch (Exception e)
        {
            AlifeLog.LogError(e);
        }
    }

    TimeSpan NextScreenDescriptionInterval()
    {
        int min = Math.Max(1, Configuration.ScreenDescriptionMinMinutes);
        int max = Math.Max(min, Configuration.ScreenDescriptionMaxMinutes);
        return TimeSpan.FromMinutes(Random.Shared.Next(min, max + 1));
    }

    bool IsIgnoredTitle(string title)
    {
        return ignoredKeywords.Any(keyword => title.Contains(keyword, StringComparison.OrdinalIgnoreCase));
    }
}
