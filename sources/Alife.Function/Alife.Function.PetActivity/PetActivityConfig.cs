namespace Alife.Function.PetActivity;

public class PetActivityConfig
{
    /// <summary>工作时间段，逗号分隔，每段格式 HH:mm-HH:mm，例如 "10:00-12:00,14:00-18:30"</summary>
    public string WorkSchedule { get; set; } = "10:00-12:00,14:00-18:30";

    // ── 状态判定 ──

    /// <summary>无键鼠操作超过该分钟数视为空闲（摸鱼/发呆）</summary>
    public double IdleMinutes { get; set; } = 5;
    /// <summary>连续操作超过该秒数视为高强度操作（WorkingPet 里的「愤怒」状态）</summary>
    public double IntenseThresholdSeconds { get; set; } = 5;
    /// <summary>两次操作间隔不超过该秒数视为仍在连续操作</summary>
    public double ActivityCooldownSeconds { get; set; } = 2;
    /// <summary>听歌判定：这些进程运行中即视为在听歌（逗号分隔，不含 .exe）</summary>
    public string MusicProcesses { get; set; } = "QQMusic,cloudmusic";

    // ── 状态 → 桌宠表现（填写桌宠支持的表情/动作名，留空表示该状态不切换）──

    public string FocusExpression { get; set; } = "";
    public string FocusMotion { get; set; } = "";
    public string IntenseExpression { get; set; } = "";
    public string IntenseMotion { get; set; } = "";
    public string SleepyExpression { get; set; } = "";
    public string SleepyMotion { get; set; } = "";
    public string MusicExpression { get; set; } = "";
    public string MusicMotion { get; set; } = "";
    public string OffWorkExpression { get; set; } = "";
    public string OffWorkMotion { get; set; } = "";

    /// <summary>让 AI 在对话时知道用户当前状态（空闲/高强度操作/听歌/已下班）</summary>
    public bool ReportStateToAI { get; set; } = true;

    // ── 主动提醒（由 AI 按自己的风格说出来）──

    public bool EnableReminders { get; set; } = true;
    /// <summary>连续操作电脑超过该分钟数后提醒起来活动</summary>
    public int ContinuousWorkReminderMinutes { get; set; } = 120;
    /// <summary>距下班不足该分钟数时提醒收尾</summary>
    public int OffWorkSoonMinutes { get; set; } = 15;

    // ── 待机行为 ──

    /// <summary>空闲时桌宠偶尔散步，并提及桌面上的一个文件名（只读文件名，不读内容）</summary>
    public bool AutonomousIdle { get; set; } = true;
    public int DesktopPeekIntervalMinutes { get; set; } = 5;

    // ── 钉钉 ──

    /// <summary>DingTalk.exe 的完整路径，留空则依赖系统能直接找到 DingTalk.exe</summary>
    public string DingTalkPath { get; set; } = "";

    // ── 快速记录 ──

    /// <summary>全局快捷键，弹出快速记录框；留空则不注册。格式如 Ctrl+Alt+W</summary>
    public string QuickRecordHotkey { get; set; } = "Ctrl+Alt+W";

    // ── 检测到工作时的确认弹窗 ──

    public bool EnableWorkDetectedPopup { get; set; } = true;
    /// <summary>两次弹窗的最短间隔（分钟）</summary>
    public int WorkPopupCooldownMinutes { get; set; } = 20;
    /// <summary>弹窗无操作后自动关闭的秒数</summary>
    public int WorkPopupSeconds { get; set; } = 20;
}
