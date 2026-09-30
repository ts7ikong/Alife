using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Data.Sqlite;

namespace Alife.Function.BehaviorCollector;

/// <summary>
/// 读取 Chrome 最近的浏览历史。Chrome 运行时会锁定 History 文件，
/// 所以先复制到临时目录再读取，用完即删。
/// </summary>
public class BrowserHistoryCollector(BehaviorStore store, BehaviorCollectorConfig config)
{
    const int MaxEntries = 500;
    // 1601-01-01 到 Unix 纪元的秒数（Chrome 时间戳起点）
    const long ChromeEpochOffsetSeconds = 11644473600;

    string HistoryPath => string.IsNullOrWhiteSpace(config.ChromeHistoryPath)
        ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Google", "Chrome", "User Data", "Default", "History")
        : config.ChromeHistoryPath;

    public Task TickAsync(CancellationToken cancellationToken)
    {
        if (File.Exists(HistoryPath) == false)
            return Task.CompletedTask;

        List<BrowserEntry> visits = ReadRecentVisits(Math.Max(1, config.BrowserIntervalMinutes));
        if (visits.Count == 0)
            return Task.CompletedTask;

        string now = DateTime.Now.ToString("HH:mm");
        store.AppendToday<BrowserEntry>(BehaviorStore.BrowserFile, entries => {
            foreach (BrowserEntry visit in visits)
            {
                if (entries.Exists(existing => existing.Url == visit.Url))
                    continue;
                visit.Time = now;
                entries.Add(visit);
            }
        }, MaxEntries);
        return Task.CompletedTask;
    }

    // 独立成方法且禁止内联：Microsoft.Data.Sqlite 缺失时，异常在调用处抛出并被 CollectorLoop 记录
    [MethodImpl(MethodImplOptions.NoInlining)]
    List<BrowserEntry> ReadRecentVisits(int minutes)
    {
        string tempPath = Path.Combine(Path.GetTempPath(), $"alife_chrome_history_{Guid.NewGuid():N}.db");
        File.Copy(HistoryPath, tempPath);
        try
        {
            long cutoff = (DateTimeOffset.UtcNow.ToUnixTimeSeconds() - minutes * 60L + ChromeEpochOffsetSeconds) * 1_000_000;
            List<BrowserEntry> visits = [];
            HashSet<string> seen = [];

            using (SqliteConnection connection = new($"Data Source={tempPath};Mode=ReadOnly;Pooling=False"))
            {
                connection.Open();
                using SqliteCommand command = connection.CreateCommand();
                command.CommandText = """
                                      SELECT urls.url, urls.title
                                      FROM visits JOIN urls ON visits.url = urls.id
                                      WHERE visits.visit_time > $cutoff
                                      ORDER BY visits.visit_time DESC
                                      LIMIT 100
                                      """;
                command.Parameters.AddWithValue("$cutoff", cutoff);
                using SqliteDataReader reader = command.ExecuteReader();
                while (reader.Read())
                {
                    string url = reader.GetString(0);
                    if (seen.Add(url))
                        visits.Add(new BrowserEntry { Url = url, Title = reader.IsDBNull(1) ? "" : reader.GetString(1) });
                }
            }
            return visits;
        }
        finally
        {
            File.Delete(tempPath);
        }
    }
}
