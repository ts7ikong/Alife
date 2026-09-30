using System;
using System.Threading;
using System.Threading.Tasks;
using Alife.Foundation;

namespace Alife.Function.BehaviorCollector;

static class CollectorLoop
{
    /// <summary>
    /// 先等待 initialDelay，之后每轮执行 tick 并等待 interval()。
    /// 单次 tick 抛出的异常只记录日志，不会中断循环，任一采集器出错都不影响其他采集器。
    /// </summary>
    public static async Task Run(TimeSpan initialDelay, Func<TimeSpan> interval, Func<CancellationToken, Task> tick, CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(initialDelay, cancellationToken);
            while (cancellationToken.IsCancellationRequested == false)
            {
                try
                {
                    await tick(cancellationToken);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception e)
                {
                    AlifeLog.LogWarning(e);
                }

                await Task.Delay(interval(), cancellationToken);
            }
        }
        catch (OperationCanceledException) { }
    }
}
