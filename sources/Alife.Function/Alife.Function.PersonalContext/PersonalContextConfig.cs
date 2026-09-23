namespace Alife.Function.PersonalContext;

public class PersonalContextConfig
{
    /// <summary>
    /// 个人档案文本，注入为系统提示，让AI充分了解你的背景、工作、偏好等
    /// </summary>
    public string PersonalProfile { get; set; } = "";

    /// <summary>
    /// WorkingPet数据文件夹路径（包含work_log.json、context_log.json等文件的目录）
    /// </summary>
    public string WorkingPetDataPath { get; set; } = "";

    /// <summary>
    /// 是否在情境摘要中注入今日工作记录
    /// </summary>
    public bool InjectWorkLog { get; set; } = true;

    /// <summary>
    /// 是否在情境摘要中注入最近屏幕活动
    /// </summary>
    public bool InjectScreenContext { get; set; } = true;

    /// <summary>
    /// 屏幕活动取最近N条记录
    /// </summary>
    public int ScreenContextCount { get; set; } = 5;

    /// <summary>
    /// 工作时间段，逗号分隔，每段格式 HH:mm-HH:mm，用于判断当前是否在工作时间
    /// </summary>
    public string WorkSchedule { get; set; } = "10:00-12:00,14:00-18:30";
}
