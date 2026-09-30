using System;
using System.Threading;
using System.Threading.Tasks;

namespace Alife.Function.BehaviorCollector;

/// <summary>
/// 鼠标活跃度：每 200ms 轮询一次光标位置累计移动距离，并对按键按下沿计数，
/// 每分钟汇总写入一条。只保存聚合数值，不保存任何坐标轨迹。
/// </summary>
public class MouseCollector(BehaviorStore store)
{
    const int MaxEntries = 120;
    static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(200);
    static readonly TimeSpan FlushInterval = TimeSpan.FromMinutes(1);

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        double distance = 0;
        int clicks = 0;
        (int x, int y)? previous = null;
        bool wasDown = false;
        DateTime nextFlush = DateTime.Now + FlushInterval;

        using PeriodicTimer timer = new(PollInterval);
        try
        {
            while (await timer.WaitForNextTickAsync(cancellationToken))
            {
                (int x, int y) position = Win32.GetCursorPosition();
                if (previous != null)
                {
                    double dx = position.x - previous.Value.x;
                    double dy = position.y - previous.Value.y;
                    distance += Math.Sqrt(dx * dx + dy * dy);
                }
                previous = position;

                bool isDown = Win32.IsMouseButtonDown();
                if (isDown && wasDown == false)
                    clicks++;
                wasDown = isDown;

                if (DateTime.Now < nextFlush)
                    continue;
                nextFlush = DateTime.Now + FlushInterval;

                if ((int)distance == 0 && clicks == 0)
                    continue;

                MouseEntry entry = new() {
                    Time = DateTime.Now.ToString("HH:mm"),
                    MoveDistance = (int)distance,
                    Clicks = clicks
                };
                store.UpdateToday(day => {
                    day.Mouse.Add(entry);
                    if (day.Mouse.Count > MaxEntries)
                        day.Mouse.RemoveRange(0, day.Mouse.Count - MaxEntries);
                });
                distance = 0;
                clicks = 0;
                previous = null; // 与 WorkingPet 一致：跨汇总周期不累计首个位移
            }
        }
        catch (OperationCanceledException) { }
    }
}
