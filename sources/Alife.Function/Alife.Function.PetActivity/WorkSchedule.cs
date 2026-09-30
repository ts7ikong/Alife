using System;
using System.Collections.Generic;
using System.Linq;

namespace Alife.Function.PetActivity;

/// <summary>由 "10:00-12:00,14:00-18:30" 形式的字符串解析出的工作时间段</summary>
public class WorkSchedule
{
    public WorkSchedule(string schedule)
    {
        foreach (string segment in schedule.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            string[] parts = segment.Split('-', StringSplitOptions.TrimEntries);
            if (parts.Length != 2 || TimeSpan.TryParse(parts[0], out TimeSpan start) == false ||
                TimeSpan.TryParse(parts[1], out TimeSpan end) == false || end <= start)
                throw new FormatException($"工作时间段「{segment}」格式不正确，应为 HH:mm-HH:mm，且结束晚于开始");
            segments.Add((start, end));
        }
        segments.Sort((a, b) => a.start.CompareTo(b.start));
    }

    public bool IsEmpty => segments.Count == 0;

    /// <summary>最后一段的开始时间（通常是下午上班），只有一段时为 null</summary>
    public TimeSpan? LastSegmentStart => segments.Count >= 2 ? segments[^1].start : null;

    public bool IsWorkTime(DateTime now)
    {
        TimeSpan time = now.TimeOfDay;
        return segments.Any(s => time >= s.start && time < s.end);
    }

    /// <summary>已过当天最后一段的结束时间</summary>
    public bool IsAfterWork(DateTime now)
    {
        return segments.Count > 0 && now.TimeOfDay >= segments[^1].end;
    }

    /// <summary>距当天最后一段结束还有多久，已结束时为负数或零；无时间段时为 null</summary>
    public TimeSpan? UntilWorkEnd(DateTime now)
    {
        return segments.Count == 0 ? null : segments[^1].end - now.TimeOfDay;
    }

    readonly List<(TimeSpan start, TimeSpan end)> segments = [];
}
