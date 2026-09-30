using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Alife.Function.BehaviorCollector;

/// <summary>
/// 每 30 秒采样一次前台窗口标题，每 2 分钟汇总成一句「你在做什么」。
/// 娱乐内容与泛网页浏览不会被当成工作；汇总结果落盘到 activity_log.json，并触发 <see cref="SummaryChanged"/>。
/// </summary>
public class ActivityMonitor(BehaviorStore store, Func<string, bool> isIgnoredTitle)
{
    public static readonly TimeSpan SampleInterval = TimeSpan.FromSeconds(30);
    static readonly TimeSpan SummaryInterval = TimeSpan.FromMinutes(2);
    const int MaxSamples = 8;
    const int MaxEntries = 200;

    // 从上到下优先匹配
    static readonly (string[] keywords, string label)[] AppRules = [
        (["visual studio code", "vscode", "cursor", "windsurf"], "写代码"),
        (["pycharm", "intellij", "idea", "webstorm", "goland", "clion", "rider", "android studio", "visual studio"], "写代码"),
        (["微信", "wechat"], "微信"),
        (["钉钉", "dingtalk"], "钉钉"),
        (["飞书", "feishu", "lark"], "飞书"),
        (["zoom", "腾讯会议", "tencent meeting", "teams", "webex"], "视频会议"),
        (["outlook", "foxmail", "thunderbird"], "邮件"),
        (["word", "excel", "powerpoint", "wps", "金山文档"], "编辑文档"),
        (["postman", "apifox", "swagger", "insomnia", "apipost"], "接口调试"),
        (["figma", "sketch", "photoshop", "illustrator"], "设计"),
        (["notion", "obsidian", "typora", "onenote", "有道云笔记", "印象笔记"], "写文档"),
        (["xmind", "mindmaster", "mindmanager"], "整理思路"),
        (["slack", "discord"], "团队沟通"),
        (["chrome", "google chrome", "edge", "msedge", "firefox", "chromium"], "浏览网页"),
        (["cmd", "命令提示符", "powershell", "terminal", "bash", "wsl", "mintty"], "命令行"),
    ];

    static readonly string[] EntertainmentKeywords = [
        "抖音", "douyin", "tiktok", "bilibili", "哔哩哔哩", "b站", "小红书",
        "微博", "知乎", "淘宝", "京东", "拼多多", "steam", "wegame", "游戏",
        "直播", "综艺", "电视剧", "电影", "小说", "斗鱼", "虎牙",
    ];

    static readonly string[] WorkSignals = [
        "写代码", "命令行", "visual studio code", "pycharm", "intellij", "vscode", "cursor",
        "windsurf", "terminal", "powershell", "文档", "excel", "word",
    ];

    /// <summary>产生了非空的工作摘要时触发，参数为摘要文本</summary>
    public event Action<string>? SummaryChanged;

    /// <summary>最近一次汇总的工作摘要，没有识别出工作时为空</summary>
    public string CurrentSummary { get; private set; } = "";

    readonly List<string> samples = [];
    DateTime lastSummaryTime = DateTime.Now;

    public Task TickAsync(CancellationToken cancellationToken)
    {
        (string processName, string title)? app = Win32.GetForegroundApp();
        if (app != null && app.Value.title.Length > 0 && isIgnoredTitle(app.Value.title) == false)
        {
            samples.Add(app.Value.title);
            if (samples.Count > MaxSamples)
                samples.RemoveAt(0);
        }

        if (DateTime.Now - lastSummaryTime >= SummaryInterval)
        {
            lastSummaryTime = DateTime.Now;
            Summarize();
        }
        return Task.CompletedTask;
    }

    void Summarize()
    {
        if (samples.Count == 0)
            return;

        List<string> candidates = samples
            .Where(title => ContainsAny(title, EntertainmentKeywords) == false)
            .ToList();

        // 整个片段都是娱乐：落盘标记并直接放弃
        if (candidates.Count == 0)
        {
            Save("", "non_work", "high", samples);
            CurrentSummary = "";
            return;
        }

        // 只有泛浏览而没有明确的工作信号，不能凭标题猜成工作
        if (candidates.Any(title => ContainsAny(title, WorkSignals)) == false)
        {
            Save("", "uncertain", "low", candidates);
            CurrentSummary = "";
            return;
        }

        Dictionary<string, int> counts = [];
        foreach (string title in candidates)
        {
            string label = LabelFromTitle(title);
            if (label.Length == 0 || label.StartsWith("浏览") || label is "微信" or "钉钉")
                continue;
            counts[label] = counts.GetValueOrDefault(label) + 1;
        }

        string summary = string.Join("  ·  ", counts.OrderByDescending(pair => pair.Value).Take(2).Select(pair => pair.Key));
        CurrentSummary = summary;
        Save(summary, summary.Length > 0 ? "work" : "uncertain", summary.Length > 0 ? "medium" : "low", candidates);
        if (summary.Length > 0)
            SummaryChanged?.Invoke(summary);
    }

    void Save(string summary, string kind, string confidence, IEnumerable<string> evidence)
    {
        ActivityEntry entry = new() {
            Time = DateTime.Now.ToString("HH:mm"),
            Summary = summary,
            Kind = kind,
            Confidence = confidence,
            Evidence = evidence.TakeLast(4).ToList()
        };
        store.AppendToday<ActivityEntry>(BehaviorStore.ActivityLogFile, entries => entries.Add(entry), MaxEntries);
    }

    /// <summary>把窗口标题解析成可读的活动标签，如「写代码 (Program.cs)」</summary>
    public static string LabelFromTitle(string title)
    {
        string lower = title.ToLowerInvariant();
        string[] parts = title.Split(" - ", StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

        foreach ((string[] keywords, string label) in AppRules)
        {
            if (keywords.Any(lower.Contains) == false)
                continue;

            // VS Code 标题格式："文件名 - 项目 - Visual Studio Code"
            if (label == "写代码" && parts.Length > 0 && parts[0].Contains('.') && parts[0].Length < 40)
                return $"写代码 ({parts[0]})";
            // 浏览器标题格式："页面标题 - 站点 - Chrome"
            if (label == "浏览网页" && parts.Length >= 2 && parts[0].Length < 20)
                return $"浏览 {parts[0]}";
            return label;
        }

        string name = parts.Length > 0 ? parts[^1] : title;
        return name.Length > 12 ? name[..12] : name;
    }

    static bool ContainsAny(string text, string[] keywords)
    {
        string lower = text.ToLowerInvariant();
        return keywords.Any(lower.Contains);
    }
}
