using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Alife.Function.PersonalContext;

public class WorkLogEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("content")] public string Content { get; set; } = "";
}

public class ScreenshotEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("text")] public string Text { get; set; } = "";
}

public class ContextLog
{
    [JsonPropertyName("screenshots")] public List<ScreenshotEntry> Screenshots { get; set; } = [];
}

public class ActivityEntry
{
    [JsonPropertyName("time")] public string Time { get; set; } = "";
    [JsonPropertyName("summary")] public string Summary { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("evidence")] public List<string> Evidence { get; set; } = [];
}

public class ActivityLog
{
    [JsonPropertyName("date")] public string Date { get; set; } = "";
    [JsonPropertyName("entries")] public List<ActivityEntry> Entries { get; set; } = [];
}

public class DayBehavior
{
    [JsonPropertyName("apps")] public Dictionary<string, int> Apps { get; set; } = [];
    [JsonPropertyName("window_titles")] public List<string> WindowTitles { get; set; } = [];
}

public class WorkingPetReader(string dataPath)
{
    static readonly JsonSerializerOptions JsonOptions = new() {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public Dictionary<string, List<WorkLogEntry>> ReadWorkLog()
    {
        return Read<Dictionary<string, List<WorkLogEntry>>>("work_log.json") ?? [];
    }

    public void WriteWorkLog(Dictionary<string, List<WorkLogEntry>> data)
    {
        string path = Path.Combine(dataPath, "work_log.json");
        File.WriteAllText(path, JsonSerializer.Serialize(data, JsonOptions));
    }

    public ContextLog ReadContextLog()
    {
        return Read<ContextLog>("context_log.json") ?? new();
    }

    public ActivityLog ReadActivityLog()
    {
        return Read<ActivityLog>("activity_log.json") ?? new();
    }

    public Dictionary<string, DayBehavior> ReadRawBehavior()
    {
        return Read<Dictionary<string, DayBehavior>>("raw_behavior.json") ?? [];
    }

    T? Read<T>(string fileName)
    {
        string path = Path.Combine(dataPath, fileName);
        if (!File.Exists(path)) return default;
        try
        {
            return JsonSerializer.Deserialize<T>(File.ReadAllText(path), JsonOptions);
        }
        catch (Exception)
        {
            return default;
        }
    }
}
