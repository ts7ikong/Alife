using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using Alife.Foundation;

namespace Alife.Function.WorkReport;

/// <summary>
/// 读取「行为采集」模块落盘的数据，整理成日报的辅助参考文字。
/// 只做尽力而为的补充：文件不存在或损坏时跳过该部分，不影响日报本身。
/// </summary>
static class BehaviorSnapshot
{
    public static string BuildDailyReference(string dataPath)
    {
        string today = DateTime.Now.ToString("yyyy-MM-dd");
        StringBuilder sb = new();

        using (JsonDocument? activity = Load(dataPath, "activity_log.json"))
        {
            if (activity != null && activity.RootElement.TryGetProperty("date", out JsonElement date) &&
                date.GetString() == today && activity.RootElement.TryGetProperty("entries", out JsonElement entries))
            {
                List<string> works = [];
                foreach (JsonElement entry in entries.EnumerateArray())
                {
                    string summary = entry.TryGetProperty("summary", out JsonElement s) ? s.GetString() ?? "" : "";
                    string kind = entry.TryGetProperty("kind", out JsonElement k) ? k.GetString() ?? "" : "";
                    string time = entry.TryGetProperty("time", out JsonElement t) ? t.GetString() ?? "" : "";
                    if (kind == "work" && summary.Length > 0)
                        works.Add($"{time} {summary}");
                }
                if (works.Count > 0)
                    sb.AppendLine($"识别到的工作活动：{string.Join("；", works.TakeLast(12))}");
            }
        }

        using (JsonDocument? behavior = Load(dataPath, "raw_behavior.json"))
        {
            if (behavior != null && behavior.RootElement.TryGetProperty(today, out JsonElement day) &&
                day.TryGetProperty("apps", out JsonElement apps))
            {
                string top = string.Join("、", apps.EnumerateObject()
                    .OrderByDescending(app => app.Value.GetInt32())
                    .Take(5)
                    .Select(app => $"{app.Name} {Math.Max(1, app.Value.GetInt32() / 60)}分钟"));
                if (top.Length > 0)
                    sb.AppendLine($"应用使用时长：{top}");
            }
        }

        using (JsonDocument? context = Load(dataPath, "context_log.json"))
        {
            if (context != null && context.RootElement.TryGetProperty("screenshots", out JsonElement shots))
            {
                string[] texts = shots.EnumerateArray()
                    .TakeLast(5)
                    .Select(shot => $"{shot.GetProperty("time").GetString()} {shot.GetProperty("text").GetString()}")
                    .ToArray();
                if (texts.Length > 0)
                    sb.AppendLine($"屏幕描述：{string.Join("；", texts)}");
            }
        }

        return sb.ToString().TrimEnd();
    }

    static JsonDocument? Load(string dataPath, string fileName)
    {
        string path = Path.Combine(dataPath, fileName);
        if (File.Exists(path) == false)
            return null;
        try
        {
            return JsonDocument.Parse(File.ReadAllText(path));
        }
        catch (JsonException e)
        {
            AlifeLog.LogWarning($"读取行为采集数据「{fileName}」失败，已跳过：{e.Message}");
            return null;
        }
    }
}
