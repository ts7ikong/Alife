using System;

namespace Alife.Function.PetActivity;

public enum PetState
{
    /// <summary>正常工作</summary>
    Focus,
    /// <summary>连续高强度操作</summary>
    Intense,
    /// <summary>空闲（摸鱼）</summary>
    Sleepy,
    /// <summary>听歌</summary>
    Music,
    /// <summary>已下班</summary>
    OffWork,
}

/// <summary>
/// 根据键鼠空闲时长、连续操作时长、是否在听歌和工作时间判定桌宠状态。
/// 优先级：下班 > 高强度操作 > 空闲 > 听歌 > 正常工作，与 WorkingPet 一致。
/// </summary>
public class PetStateMachine(PetActivityConfig config, WorkSchedule schedule)
{
    /// <summary>当前连续操作已持续的时长，未在连续操作时为 null</summary>
    public TimeSpan? GetContinuousActivity(DateTime now) => activityStart == null ? null : now - activityStart.Value;

    public DateTime? ActivityStart => activityStart;

    public PetState Update(DateTime now, double idleSeconds, bool musicPlaying)
    {
        // 连续操作计时：空闲超过阈值时清零，操作间隔在冷却内时开始计时
        if (idleSeconds > config.IntenseThresholdSeconds)
            activityStart = null;
        else if (idleSeconds <= config.ActivityCooldownSeconds && activityStart == null)
            activityStart = now;

        if (schedule.IsAfterWork(now))
            return PetState.OffWork;

        bool isActive = idleSeconds <= config.ActivityCooldownSeconds;
        if (isActive && activityStart != null &&
            (now - activityStart.Value).TotalSeconds >= config.IntenseThresholdSeconds)
            return PetState.Intense;

        if (idleSeconds >= config.IdleMinutes * 60)
            return PetState.Sleepy;

        return musicPlaying ? PetState.Music : PetState.Focus;
    }

    DateTime? activityStart;
}
