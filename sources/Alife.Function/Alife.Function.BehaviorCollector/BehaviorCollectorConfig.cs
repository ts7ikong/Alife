namespace Alife.Function.BehaviorCollector;

public class BehaviorCollectorConfig
{
    /// <summary>采集数据目录，留空则使用 存储目录/WorkingPet。PersonalContext 的「WorkingPet数据目录」填同一路径即可读取这些数据</summary>
    public string DataPath { get; set; } = "";

    /// <summary>统计前台应用使用时长</summary>
    public bool EnableAppUsage { get; set; } = true;
    /// <summary>应用使用时长采样间隔（秒）</summary>
    public int AppUsageIntervalSeconds { get; set; } = 30;

    /// <summary>统计鼠标活跃度（仅移动距离与点击次数，不记录坐标轨迹）</summary>
    public bool EnableMouse { get; set; } = true;

    /// <summary>通过剪贴板捕获中文输入片段</summary>
    public bool EnableClipboardInput { get; set; } = true;

    /// <summary>定时截屏 OCR（使用 Windows 自带 OCR，仅保存文字）</summary>
    public bool EnableOcr { get; set; } = true;
    /// <summary>OCR 间隔（分钟）</summary>
    public int OcrIntervalMinutes { get; set; } = 3;

    /// <summary>读取 Chrome 浏览历史</summary>
    public bool EnableBrowserHistory { get; set; } = true;
    /// <summary>浏览历史读取间隔（分钟）</summary>
    public int BrowserIntervalMinutes { get; set; } = 30;
    /// <summary>Chrome History 文件路径，留空使用默认位置</summary>
    public string ChromeHistoryPath { get; set; } = "";

    /// <summary>定时截屏并用视觉模型生成一句话描述（需要启用视觉模型；只保存文字，不保存截图）</summary>
    public bool EnableScreenDescription { get; set; } = true;
    /// <summary>截屏描述最小间隔（分钟）</summary>
    public int ScreenDescriptionMinMinutes { get; set; } = 15;
    /// <summary>截屏描述最大间隔（分钟）</summary>
    public int ScreenDescriptionMaxMinutes { get; set; } = 30;

    /// <summary>根据前台窗口标题识别正在做的工作（用于「检测到工作」提示）</summary>
    public bool EnableActivityMonitor { get; set; } = true;

    /// <summary>窗口标题包含这些关键词（逗号分隔）时不参与统计，用于排除桌宠自身窗口</summary>
    public string IgnoredTitleKeywords { get; set; } = "Alife,WorkingPet,桌宠";
}
