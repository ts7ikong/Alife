using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Alife.Function.BehaviorCollector;

/// <summary>按天累计前台应用使用秒数，并记录出现过的窗口标题（最多保留 100 条）</summary>
public class AppUsageCollector(BehaviorStore store, BehaviorCollectorConfig config, Func<string, bool> isIgnoredTitle)
{
    const int MaxTitles = 100;

    public Task TickAsync(CancellationToken cancellationToken)
    {
        (string processName, string title)? app = Win32.GetForegroundApp();
        if (app == null || isIgnoredTitle(app.Value.title))
            return Task.CompletedTask;

        int interval = Math.Max(1, config.AppUsageIntervalSeconds);
        store.UpdateToday(day => {
            day.Apps[app.Value.processName] = day.Apps.GetValueOrDefault(app.Value.processName) + interval;
            string title = app.Value.title;
            if (title.Length > 0 && day.WindowTitles.Contains(title) == false)
            {
                day.WindowTitles.Add(title);
                if (day.WindowTitles.Count > MaxTitles)
                    day.WindowTitles = day.WindowTitles.TakeLast(MaxTitles).ToList();
            }
        });
        return Task.CompletedTask;
    }
}
