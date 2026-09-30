using System;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Alife.Foundation;
using Alife.Function.AIModelUtility;

namespace Alife.Function.BehaviorCollector;

/// <summary>定时截屏，交给视觉模型生成一句话描述后只保存文字，截图用完即删</summary>
public class ScreenDescriptionCollector(BehaviorStore store, IVisionModel visionModel)
{
    const int MaxDescriptions = 10;
    const string Question = "简短描述这个桌面在做什么，30字以内，不要提及任何账号密码或敏感信息，只说大概在做什么工作或活动。";

    public async Task TickAsync(CancellationToken cancellationToken)
    {
        string path = Path.Combine(AlifePath.TempFolderPath, $"behavior_screen_{Guid.NewGuid():N}.png");
        try
        {
            using (Bitmap screen = Win32.CaptureScreen())
                screen.Save(path, System.Drawing.Imaging.ImageFormat.Png);

            string description = (await visionModel.QueryAsync(path, Question, 100, cancellationToken)).Trim();
            if (description.Length == 0)
                return;

            TextEntry entry = new() { Time = DateTime.Now.ToString("HH:mm"), Text = description };
            store.Update<ContextLog>(BehaviorStore.ContextLogFile, log => {
                log.Screenshots.Add(entry);
                if (log.Screenshots.Count > MaxDescriptions)
                    log.Screenshots.RemoveRange(0, log.Screenshots.Count - MaxDescriptions);
            });
        }
        finally
        {
            File.Delete(path);
        }
    }
}
