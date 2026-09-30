using System;
using System.Runtime.InteropServices;
using System.Threading;

namespace Alife.Function.PetActivity;

/// <summary>
/// 系统级全局快捷键。RegisterHotKey 要求注册与消息循环在同一线程，所以这里独占一个线程。
/// </summary>
public sealed class GlobalHotkey : IDisposable
{
    /// <summary>解析 "Ctrl+Alt+W" 形式的快捷键，支持 Ctrl/Alt/Shift/Win 修饰键与单个字母、数字或 F1-F12</summary>
    public static (uint modifiers, uint key) Parse(string hotkey)
    {
        uint modifiers = 0;
        uint key = 0;
        foreach (string token in hotkey.Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            switch (token.ToLowerInvariant())
            {
                case "alt": modifiers |= 0x1; break;
                case "ctrl" or "control": modifiers |= 0x2; break;
                case "shift": modifiers |= 0x4; break;
                case "win": modifiers |= 0x8; break;
                default:
                    if (key != 0)
                        throw new FormatException($"快捷键「{hotkey}」包含多个主键");
                    if (token.Length == 1 && char.IsAsciiLetterOrDigit(token[0]))
                        key = char.ToUpperInvariant(token[0]);
                    else if (token.Length is 2 or 3 && (token[0] is 'F' or 'f') &&
                             int.TryParse(token[1..], out int number) && number is >= 1 and <= 12)
                        key = (uint)(0x70 + number - 1);
                    else
                        throw new FormatException($"快捷键「{hotkey}」中的「{token}」无法识别");
                    break;
            }
        }
        if (key == 0 || modifiers == 0)
            throw new FormatException($"快捷键「{hotkey}」需要至少一个修饰键和一个主键");
        return (modifiers, key);
    }

    /// <summary>注册失败（例如快捷键已被其他程序占用）时抛出异常</summary>
    public GlobalHotkey(string hotkey, Action onPressed)
    {
        (uint modifiers, uint key) = Parse(hotkey);
        Exception? failure = null;
        using ManualResetEventSlim ready = new();

        thread = new Thread(() => {
            threadId = GetCurrentThreadId();
            if (RegisterHotKey(IntPtr.Zero, HotkeyId, modifiers | ModNoRepeat, key) == false)
                failure = new InvalidOperationException($"注册全局快捷键「{hotkey}」失败，可能已被其他程序占用", 
                    new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error()));
            ready.Set();
            if (failure != null)
                return;

            try
            {
                while (GetMessage(out MSG message, IntPtr.Zero, 0, 0) > 0)
                {
                    if (message.message == WmHotkey)
                        onPressed();
                }
            }
            finally
            {
                UnregisterHotKey(IntPtr.Zero, HotkeyId);
            }
        }) { IsBackground = true, Name = "PetActivity.GlobalHotkey" };
        thread.Start();
        ready.Wait();

        if (failure != null)
            throw failure;
    }

    public void Dispose()
    {
        if (thread.IsAlive)
            PostThreadMessage(threadId, WmQuit, UIntPtr.Zero, IntPtr.Zero);
    }

    readonly Thread thread;
    uint threadId;

    const int HotkeyId = 1;
    const uint ModNoRepeat = 0x4000;
    const uint WmHotkey = 0x0312;
    const uint WmQuit = 0x0012;

    [StructLayout(LayoutKind.Sequential)]
    struct MSG
    {
        public IntPtr hwnd;
        public uint message;
        public UIntPtr wParam;
        public IntPtr lParam;
        public uint time;
        public int ptX;
        public int ptY;
    }

    [DllImport("user32.dll", SetLastError = true)] static extern bool RegisterHotKey(IntPtr hWnd, int id, uint modifiers, uint vk);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr hWnd, int id);
    [DllImport("user32.dll")] static extern int GetMessage(out MSG message, IntPtr hWnd, uint filterMin, uint filterMax);
    [DllImport("user32.dll")] static extern bool PostThreadMessage(uint threadId, uint message, UIntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
}
