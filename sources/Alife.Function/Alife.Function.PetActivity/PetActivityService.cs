using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Alife.Foundation;
using Alife.Framework;
using Alife.Function.BehaviorCollector;
using Alife.Function.DeskPet;
using Alife.Function.FunctionCaller;
using Alife.Function.MessageFilter;
using Alife.Function.WorkReport;
using ElectronNET.API;
using ElectronNET.API.Entities;

namespace Alife.Function.PetActivity;

[Module("桌宠状态联动",
    "根据键鼠活跃、空闲、听歌、下班等状态切换桌宠表情动作，并提供钉钉一键启动、全局快捷键快速记录、检测到工作时的确认弹窗和主动提醒。",
    defaultCategory: "个人定制")]
public class PetActivityService(
    XmlFunctionCaller functionCaller,
    Interactor<PetActivityService> interactor,
    MessageFilterService messageFilterService) :
    ChatBehaviour,
    IConfigurable<PetActivityConfig>
{
    public PetActivityConfig Configuration { get; set; } = null!;

    /// <summary>桌宠当前状态</summary>
    public PetState CurrentState { get; private set; } = PetState.Focus;

    [XmlFunction(FunctionMode.OneShot)]
    [Description("启动钉钉")]
    public string OpenDingTalk()
    {
        string target = File.Exists(Configuration.DingTalkPath) ? Configuration.DingTalkPath : "DingTalk.exe";
        try
        {
            Process.Start(new ProcessStartInfo { FileName = target, UseShellExecute = true });
        }
        catch (System.ComponentModel.Win32Exception e)
        {
            throw new InvalidOperationException("找不到钉钉，请在「桌宠状态联动」配置中填写 DingTalkPath", e);
        }
        return "已启动钉钉。";
    }

    [XmlFunction(FunctionMode.OneShot)]
    [Description("在屏幕上弹出快速记录框，让用户自己输入一条工作记录")]
    public string ShowQuickRecord()
    {
        _ = ShowQuickRecordAsync();
        return "已弹出快速记录框。";
    }

    [XmlFunction(FunctionMode.OneShot)]
    [Description("查询用户当前的电脑使用状态（正常工作/高强度操作/空闲/听歌/已下班）以及正在做的工作")]
    public string GetUserState()
    {
        string activity = behaviorCollector?.CurrentActivity ?? "";
        return $"{DescribeState(CurrentState)}{(activity.Length > 0 ? $"；正在：{activity}" : "")}";
    }

    WorkSchedule schedule = null!;
    PetStateMachine stateMachine = null!;
    string[] musicProcesses = [];
    IDeskPet? deskPet;
    BehaviorCollectorService? behaviorCollector;
    WorkReportService? workReport;
    GlobalHotkey? hotkey;
    BrowserWindow? popup;
    DateTime lastWorkPopupTime = DateTime.MinValue;
    readonly HashSet<string> warnedNames = [];
    readonly Dictionary<string, bool> reminderFlags = [];
    string reminderFlagsDate = "";

    protected override Task OnAwake()
    {
        schedule = new WorkSchedule(Configuration.WorkSchedule);
        stateMachine = new PetStateMachine(Configuration, schedule);
        musicProcesses = Configuration.MusicProcesses
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

        // 在所有模块构造完成后再查找可选依赖，避免构造顺序影响结果
        deskPet = Find<IDeskPet>();
        behaviorCollector = Find<BehaviorCollectorService>();
        workReport = Find<WorkReportService>();

        XmlHandler handler = new(this) {
            Description = "工作助手：启动钉钉、弹出快速记录框、查询用户当前的电脑使用状态。",
            Explanation = "用户要求打开钉钉时调用 OpenDingTalk；想知道用户此刻是在忙还是在摸鱼时调用 GetUserState。"
        };
        functionCaller.RegisterHandler(handler, cancellationToken: DestroyCancellationToken);

        if (Configuration.ReportStateToAI)
            messageFilterService.AddMessageReplyGuidance(BuildStateGuidance, DestroyCancellationToken);

        Electron.IpcMain.On(PopupWindow.SubmitChannel, OnPopupSubmit);

        if (string.IsNullOrWhiteSpace(Configuration.QuickRecordHotkey) == false)
        {
            try
            {
                hotkey = new GlobalHotkey(Configuration.QuickRecordHotkey, () => _ = ShowQuickRecordAsync());
            }
            catch (InvalidOperationException e)
            {
                // 快捷键被其他程序占用属于环境问题，不应拖垮整个模块
                AlifeLog.LogError(e);
            }
        }

        if (behaviorCollector != null)
            behaviorCollector.WorkDetected += OnWorkDetected;

        _ = StateLoop();
        _ = ReminderLoop();
        return Task.CompletedTask;
    }

    protected override Task OnDestroy()
    {
        if (behaviorCollector != null)
            behaviorCollector.WorkDetected -= OnWorkDetected;
        hotkey?.Dispose();
        Electron.IpcMain.RemoveAllListeners(PopupWindow.SubmitChannel);
        try { popup?.Destroy(); } catch (Exception e) { AlifeLog.LogWarning(e); }
        popup = null;
        return Task.CompletedTask;
    }

    T? Find<T>() where T : class
    {
        foreach (object instance in ChatActivity.Container.Instances)
            if (instance is T found)
                return found;
        return null;
    }

    // ═══════════════ 状态联动 ═══════════════

    async Task StateLoop()
    {
        DateTime lastMusicCheck = DateTime.MinValue;
        bool musicPlaying = false;
        PetState? applied = null;
        DateTime lastExpression = DateTime.MinValue;
        DateTime lastMotion = DateTime.MinValue;
        DateTime lastWander = DateTime.MinValue;
        DateTime lastPeek = DateTime.Now;
        Vector2 wanderOffset = Vector2.Zero;

        using PeriodicTimer timer = new(TimeSpan.FromSeconds(2));
        try
        {
            while (await timer.WaitForNextTickAsync(DestroyCancellationToken))
            {
                try
                {
                    DateTime now = DateTime.Now;
                    if (now - lastMusicCheck >= TimeSpan.FromSeconds(10))
                    {
                        musicPlaying = IsMusicPlaying();
                        lastMusicCheck = now;
                    }

                    PetState state = stateMachine.Update(now, Win32.GetIdleSeconds(), musicPlaying);
                    CurrentState = state;

                    if (deskPet == null)
                        continue;

                    (string expression, string motion) = GetActions(state);
                    if (state != applied)
                    {
                        applied = state;
                        lastExpression = lastMotion = now;
                        await PlayExpression(expression);
                        await PlayMotion(motion);
                    }
                    else if (functionCaller.IsIdle)
                    {
                        // 桌宠的表情 3 秒后会自动回落到默认表情，状态持续期间需要定期重放才能保持
                        if (expression.Length > 0 && now - lastExpression >= TimeSpan.FromSeconds(2.5))
                        {
                            lastExpression = now;
                            await PlayExpression(expression);
                        }
                        if (motion.Length > 0 && now - lastMotion >= TimeSpan.FromSeconds(30))
                        {
                            lastMotion = now;
                            await PlayMotion(motion);
                        }
                    }

                    if (Configuration.AutonomousIdle && state == PetState.Sleepy && schedule.IsWorkTime(now))
                    {
                        if (now - lastWander >= TimeSpan.FromSeconds(45))
                        {
                            lastWander = now;
                            wanderOffset = await Wander(wanderOffset);
                        }
                        if (now - lastPeek >= TimeSpan.FromMinutes(Math.Max(1, Configuration.DesktopPeekIntervalMinutes)))
                        {
                            lastPeek = now;
                            PeekDesktop();
                        }
                    }
                }
                catch (Exception e) when (e is not OperationCanceledException)
                {
                    AlifeLog.LogWarning(e);
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    (string expression, string motion) GetActions(PetState state) => state switch {
        PetState.Focus => (Configuration.FocusExpression, Configuration.FocusMotion),
        PetState.Intense => (Configuration.IntenseExpression, Configuration.IntenseMotion),
        PetState.Sleepy => (Configuration.SleepyExpression, Configuration.SleepyMotion),
        PetState.Music => (Configuration.MusicExpression, Configuration.MusicMotion),
        PetState.OffWork => (Configuration.OffWorkExpression, Configuration.OffWorkMotion),
        _ => throw new ArgumentOutOfRangeException(nameof(state), state, null)
    };

    async Task PlayExpression(string name)
    {
        if (name.Length == 0 || IsSupported(name, deskPet!.SupportedExpressions, "表情") == false)
            return;
        await deskPet.ShowExpression(name);
    }

    async Task PlayMotion(string name)
    {
        if (name.Length == 0 || IsSupported(name, deskPet!.SupportedMotions, "动作") == false)
            return;
        await deskPet.ShowMotion(name);
    }

    /// <summary>配置的表情/动作不被当前桌宠支持时，只提醒一次，避免每 2 秒刷一次日志</summary>
    bool IsSupported(string name, string[] supported, string kind)
    {
        if (supported.Contains(name))
            return true;
        if (warnedNames.Add(kind + name))
            AlifeLog.LogWarning($"桌宠状态联动：当前桌宠不支持{kind}「{name}」，可选：{string.Join(", ", supported)}");
        return false;
    }

    /// <summary>在原地附近随机走动，累计偏移过大时反向，避免越走越远走出屏幕</summary>
    async Task<Vector2> Wander(Vector2 accumulated)
    {
        float dx = Random.Shared.Next(-140, 141);
        float dy = Random.Shared.Next(-80, 81);
        if (Math.Abs(accumulated.X) > 200 && accumulated.X * dx > 0) dx = -dx;
        if (Math.Abs(accumulated.Y) > 120 && accumulated.Y * dy > 0) dy = -dy;

        Vector2 offset = new(dx, dy);
        await deskPet!.Move(offset, 1.4f);
        return accumulated + offset;
    }

    /// <summary>只读取桌面上的文件名，不读内容，交给 AI 顺口聊一句</summary>
    void PeekDesktop()
    {
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string[] names = Directory.EnumerateFileSystemEntries(desktop)
            .Select(Path.GetFileName)
            .OfType<string>()
            .Where(name => name.StartsWith('.') == false && name.Equals("desktop.ini", StringComparison.OrdinalIgnoreCase) == false)
            .ToArray();
        if (names.Length == 0)
            return;

        interactor.Poke($"你闲着的时候注意到用户桌面上有一个叫「{names[Random.Shared.Next(names.Length)]}」的项目（你只知道名字，没有看内容），可以顺口聊一句。");
    }

    bool IsMusicPlaying()
    {
        foreach (string name in musicProcesses)
        {
            Process[] processes = Process.GetProcessesByName(name);
            bool found = processes.Length > 0;
            foreach (Process process in processes)
                process.Dispose();
            if (found)
                return true;
        }
        return false;
    }

    string BuildStateGuidance()
    {
        return CurrentState == PetState.Focus ? "" : $"[用户当前状态] {DescribeState(CurrentState)}";
    }

    static string DescribeState(PetState state) => state switch {
        PetState.Focus => "正常工作中",
        PetState.Intense => "正在连续高强度操作电脑，可能很忙，别打扰太多",
        PetState.Sleepy => "已经有一阵子没操作电脑了，可能在摸鱼或离开了",
        PetState.Music => "正在听歌",
        PetState.OffWork => "已经下班了",
        _ => throw new ArgumentOutOfRangeException(nameof(state), state, null)
    };

    // ═══════════════ 主动提醒 ═══════════════

    async Task ReminderLoop()
    {
        using PeriodicTimer timer = new(TimeSpan.FromMinutes(1));
        try
        {
            while (await timer.WaitForNextTickAsync(DestroyCancellationToken))
            {
                try
                {
                    if (Configuration.EnableReminders)
                        CheckReminders(DateTime.Now);
                }
                catch (Exception e) when (e is not OperationCanceledException)
                {
                    AlifeLog.LogWarning(e);
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    /// <summary>同一类提醒每天最多触发一次；只在工作时间内提醒</summary>
    void CheckReminders(DateTime now)
    {
        string today = now.ToString("yyyy-MM-dd");
        if (reminderFlagsDate != today)
        {
            reminderFlagsDate = today;
            reminderFlags.Clear();
        }
        if (schedule.IsWorkTime(now) == false)
            return;

        // 连续操作过久
        TimeSpan? continuous = stateMachine.GetContinuousActivity(now);
        if (continuous != null && continuous.Value.TotalMinutes >= Configuration.ContinuousWorkReminderMinutes &&
            TryFire($"continuous_{stateMachine.ActivityStart:HHmm}"))
        {
            interactor.Poke($"用户已经连续操作电脑超过 {Configuration.ContinuousWorkReminderMinutes} 分钟了，请用你的风格提醒他起来活动一下。");
            return;
        }

        // 下午上班后还没有任何工作记录
        if (workReport != null && schedule.LastSegmentStart is TimeSpan afternoon && now.TimeOfDay >= afternoon &&
            workReport.GetTodayEntryCount() == 0 && TryFire("no_record"))
        {
            interactor.Poke("已经到下午了，用户今天还没有任何工作记录，请提醒他补一下（可以直接问他上午做了什么，然后帮他记录）。");
            return;
        }

        // 临近下班
        TimeSpan? remaining = schedule.UntilWorkEnd(now);
        if (remaining != null && remaining.Value > TimeSpan.Zero &&
            remaining.Value.TotalMinutes <= Configuration.OffWorkSoonMinutes && TryFire("end_soon"))
        {
            interactor.Poke($"距离下班还有 {(int)Math.Ceiling(remaining.Value.TotalMinutes)} 分钟，请提醒用户收尾。");
        }
    }

    bool TryFire(string key)
    {
        return reminderFlags.TryAdd(key, true);
    }

    // ═══════════════ 快速记录 / 检测到工作 ═══════════════

    async Task ShowQuickRecordAsync()
    {
        try
        {
            await ShowPopup(new PopupOptions(
                "快速记录", "", "写下刚做完的事…",
                "Enter 记录 · Ctrl+Enter 问 AI · Esc 关闭",
                [new PopupButton("记录", "record"), new PopupButton("问 AI", "chat")],
                CountdownSeconds: 0, Centered: true));
        }
        catch (Exception e)
        {
            AlifeLog.LogError(e);
        }
    }

    void OnWorkDetected(string summary)
    {
        if (Configuration.EnableWorkDetectedPopup == false)
            return;
        DateTime now = DateTime.Now;
        if (now - lastWorkPopupTime < TimeSpan.FromMinutes(Configuration.WorkPopupCooldownMinutes))
            return;
        lastWorkPopupTime = now;
        _ = ShowWorkDetectedAsync(summary);
    }

    async Task ShowWorkDetectedAsync(string summary)
    {
        try
        {
            await ShowPopup(new PopupOptions(
                "检测到工作记录 🔍", summary, "", "",
                [new PopupButton("确认记录", "record"), new PopupButton("忽略", "")],
                CountdownSeconds: Math.Max(1, Configuration.WorkPopupSeconds), Centered: false,
                Width: 340, Height: 190));
        }
        catch (Exception e)
        {
            AlifeLog.LogError(e);
        }
    }

    async Task ShowPopup(PopupOptions options)
    {
        if (popup != null)
        {
            try { popup.Destroy(); } catch (Exception e) { AlifeLog.LogWarning(e); }
            popup = null;
        }

        BrowserWindow window = await PopupWindow.ShowAsync(options);
        window.OnClosed += () => {
            if (popup == window)
                popup = null;
        };
        popup = window;
    }

    void OnPopupSubmit(object? payload)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(payload?.ToString() ?? "");
            string kind = document.RootElement.GetProperty("kind").GetString() ?? "";
            string text = document.RootElement.GetProperty("text").GetString() ?? "";

            switch (kind)
            {
                case "record":
                    RecordWork(text);
                    break;
                case "chat":
                    interactor.Chat(text);
                    break;
                default:
                    throw new InvalidOperationException($"未知的弹窗提交类型：{kind}");
            }
        }
        catch (Exception e)
        {
            AlifeLog.LogError(e);
        }
    }

    void RecordWork(string text)
    {
        if (workReport == null)
        {
            interactor.Poke($"用户想记录一条工作内容，请帮他记下来：{text}");
            return;
        }

        string result = workReport.RecordWork(text);
        _ = Toast(result);
    }

    /// <summary>用桌宠字幕短暂提示一句话</summary>
    async Task Toast(string message)
    {
        if (deskPet == null)
            return;
        try
        {
            await deskPet.ShowSubtitle(message);
            await Task.Delay(TimeSpan.FromSeconds(3), DestroyCancellationToken);
            await deskPet.ShowSubtitle(null);
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            AlifeLog.LogWarning(e);
        }
    }
}
