using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Alife.Function.BehaviorCollector;

/// <summary>
/// 通过剪贴板捕获中文输入片段（输入法上屏内容常经过剪贴板）。
/// 过滤规则：至少 3 个汉字、不超过 200 字、与上一条不重复。
/// </summary>
public class ClipboardInputCollector(BehaviorStore store)
{
    const int MinChineseCount = 3;
    const int MaxLength = 200;
    const int MaxSegments = 50;

    uint lastSequence;
    string lastText = "";

    public string[] GetRecent(int count)
    {
        InputLog log = store.Read<InputLog>(BehaviorStore.InputLogFile);
        if (log.Date != DateTime.Now.ToString("yyyy-MM-dd"))
            return [];
        return log.Segments.TakeLast(count).ToArray();
    }

    public Task TickAsync(CancellationToken cancellationToken)
    {
        uint sequence = Win32.GetClipboardSequence();
        if (sequence == lastSequence)
            return Task.CompletedTask;
        lastSequence = sequence;

        string? text = Win32.ReadClipboardText()?.Trim();
        if (string.IsNullOrEmpty(text) || text == lastText || text.Length > MaxLength)
            return Task.CompletedTask;
        if (text.Count(c => c >= '一' && c <= '鿿') < MinChineseCount)
            return Task.CompletedTask;
        lastText = text;

        string today = DateTime.Now.ToString("yyyy-MM-dd");
        store.Update<InputLog>(BehaviorStore.InputLogFile, log => {
            if (log.Date != today)
            {
                log.Date = today;
                log.Segments = [];
            }
            if (log.Segments.Count > 0 && log.Segments[^1] == text)
                return;
            log.Segments.Add(text);
            if (log.Segments.Count > MaxSegments)
                log.Segments.RemoveRange(0, log.Segments.Count - MaxSegments);
        });
        return Task.CompletedTask;
    }
}
