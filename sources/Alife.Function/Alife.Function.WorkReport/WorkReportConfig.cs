namespace Alife.Function.WorkReport;

public class WorkReportConfig
{
    /// <summary>自动触发日报的时间，格式 HH:mm</summary>
    public string DailyReportTime { get; set; } = "18:00";
    /// <summary>自动触发周报的星期几，0=周一，4=周五</summary>
    public int WeeklyReportDayOfWeek { get; set; } = 4;
    /// <summary>自动触发周报的时间，格式 HH:mm</summary>
    public string WeeklyReportTime { get; set; } = "17:30";
    /// <summary>是否启用每日自动日报</summary>
    public bool AutoDailyReport { get; set; } = true;
    /// <summary>是否启用每周自动周报</summary>
    public bool AutoWeeklyReport { get; set; } = true;
}
