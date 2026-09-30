using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Alife.Function.BehaviorCollector;

// 以下数据模型的 JSON 结构与 WorkingPet 保持一致，PersonalContext / WorkReport 可直接读取

public class TextEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("text")] public string Text { get; set; } = "";
}

public class BrowserEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("url")] public string Url { get; set; } = "";
    [JsonPropertyName("title")] public string Title { get; set; } = "";
}

public class ActivityEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("summary")] public string Summary { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("confidence")] public string Confidence { get; set; } = "";
    [JsonPropertyName("evidence")] public List<string> Evidence { get; set; } = [];
}

public class MouseEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("move_distance")] public int MoveDistance { get; set; }
    [JsonPropertyName("clicks")] public int Clicks { get; set; }
}

public class DayBehavior
{
    [JsonPropertyName("apps")] public Dictionary<string, int> Apps { get; set; } = [];
    [JsonPropertyName("window_titles")] public List<string> WindowTitles { get; set; } = [];
    [JsonPropertyName("mouse")] public List<MouseEntry> Mouse { get; set; } = [];
}

/// <summary>按日期归档的条目文件（ocr_log.json / browser_history.json / activity_log.json）</summary>
public class DayEntries<T>
{
    [JsonPropertyName("date")] public string Date { get; set; } = "";
    [JsonPropertyName("entries")] public List<T> Entries { get; set; } = [];
}

/// <summary>中文输入片段文件（input_log.json）</summary>
public class InputLog
{
    [JsonPropertyName("date")] public string Date { get; set; } = "";
    [JsonPropertyName("segments")] public List<string> Segments { get; set; } = [];
}

/// <summary>截屏描述文件（context_log.json）</summary>
public class ContextLog
{
    [JsonPropertyName("screenshots")] public List<TextEntry> Screenshots { get; set; } = [];
}

/// <summary>
/// 采集数据的 JSON 文件存取。所有采集器共用一把锁，保证同一文件不会被并发写坏。
/// </summary>
public class BehaviorStore
{
    public const string RawBehaviorFile = "raw_behavior.json";
    public const string InputLogFile = "input_log.json";
    public const string OcrLogFile = "ocr_log.json";
    public const string BrowserFile = "browser_history.json";
    public const string ContextLogFile = "context_log.json";
    public const string ActivityLogFile = "activity_log.json";

    public BehaviorStore(string dataPath)
    {
        DataPath = dataPath;
        Directory.CreateDirectory(dataPath);
    }

    public string DataPath { get; }

    static string Today => DateTime.Now.ToString("yyyy-MM-dd");

    static readonly JsonSerializerOptions JsonOptions = new() {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    readonly object gate = new();

    /// <summary>读取文件，文件不存在时返回新建的默认值；文件损坏则抛出异常，避免悄悄覆盖用户数据</summary>
    public T Read<T>(string fileName) where T : new()
    {
        lock (gate)
            return ReadUnlocked<T>(fileName);
    }

    /// <summary>读取 → 修改 → 写回，整个过程持锁</summary>
    public void Update<T>(string fileName, Action<T> mutate) where T : new()
    {
        lock (gate)
        {
            T data = ReadUnlocked<T>(fileName);
            mutate(data);
            string json = JsonSerializer.Serialize(data, JsonOptions);
            File.WriteAllText(Path.Combine(DataPath, fileName), json, Encoding.UTF8);
        }
    }

    /// <summary>对当天的条目文件追加内容，跨天时自动清空重建</summary>
    public void AppendToday<T>(string fileName, Action<List<T>> mutate, int maxEntries = 200)
    {
        Update<DayEntries<T>>(fileName, file => {
            if (file.Date != Today)
            {
                file.Date = Today;
                file.Entries = [];
            }
            mutate(file.Entries);
            if (file.Entries.Count > maxEntries)
                file.Entries.RemoveRange(0, file.Entries.Count - maxEntries);
        });
    }

    public void UpdateToday(Action<DayBehavior> mutate)
    {
        Update<Dictionary<string, DayBehavior>>(RawBehaviorFile, all => {
            if (all.TryGetValue(Today, out DayBehavior? day) == false)
            {
                day = new DayBehavior();
                all[Today] = day;
            }
            mutate(day);
        });
    }

    T ReadUnlocked<T>(string fileName) where T : new()
    {
        string path = Path.Combine(DataPath, fileName);
        if (File.Exists(path) == false)
            return new T();
        return JsonSerializer.Deserialize<T>(File.ReadAllText(path, Encoding.UTF8), JsonOptions) ?? new T();
    }
}
