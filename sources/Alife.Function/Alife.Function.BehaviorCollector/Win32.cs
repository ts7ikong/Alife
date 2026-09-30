using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;

namespace Alife.Function.BehaviorCollector;

/// <summary>
/// 采集所需的 Win32 调用集合。仅 Windows 可用。
/// </summary>
public static class Win32
{
    /// <summary>距离用户上一次键鼠输入过去的秒数</summary>
    public static double GetIdleSeconds()
    {
        LASTINPUTINFO info = new() { cbSize = (uint)Marshal.SizeOf<LASTINPUTINFO>() };
        if (GetLastInputInfo(ref info) == false)
            throw new InvalidOperationException("GetLastInputInfo 调用失败");
        // dwTime 与 TickCount 均为 32 位毫秒计数，回绕时依然可用无符号减法得到正确差值
        uint elapsed = unchecked((uint)Environment.TickCount - info.dwTime);
        return elapsed / 1000.0;
    }

    /// <summary>获取前台窗口所属进程名（含 .exe）与窗口标题，无前台窗口时返回 null</summary>
    public static (string processName, string title)? GetForegroundApp()
    {
        IntPtr hwnd = GetForegroundWindow();
        if (hwnd == IntPtr.Zero)
            return null;

        int length = GetWindowTextLength(hwnd);
        StringBuilder builder = new(length + 1);
        GetWindowText(hwnd, builder, builder.Capacity);

        GetWindowThreadProcessId(hwnd, out uint pid);
        try
        {
            using Process process = Process.GetProcessById((int)pid);
            return (process.ProcessName + ".exe", builder.ToString().Trim());
        }
        catch (ArgumentException)
        {
            // 进程在取到句柄后恰好退出
            return null;
        }
    }

    public static (int x, int y) GetCursorPosition()
    {
        if (GetCursorPos(out POINT point) == false)
            throw new InvalidOperationException("GetCursorPos 调用失败");
        return (point.X, point.Y);
    }

    /// <summary>鼠标左/右/中键当前是否按下</summary>
    public static bool IsMouseButtonDown()
    {
        return (GetAsyncKeyState(0x01) & 0x8000) != 0 ||
               (GetAsyncKeyState(0x02) & 0x8000) != 0 ||
               (GetAsyncKeyState(0x04) & 0x8000) != 0;
    }

    /// <summary>剪贴板内容变化计数，用于避免无意义地打开剪贴板</summary>
    public static uint GetClipboardSequence() => GetClipboardSequenceNumber();

    /// <summary>读取剪贴板文本，剪贴板被占用或不含文本时返回 null</summary>
    public static string? ReadClipboardText()
    {
        if (OpenClipboard(IntPtr.Zero) == false)
            return null;
        try
        {
            IntPtr handle = GetClipboardData(CF_UNICODETEXT);
            if (handle == IntPtr.Zero)
                return null;
            IntPtr pointer = GlobalLock(handle);
            if (pointer == IntPtr.Zero)
                return null;
            try
            {
                return Marshal.PtrToStringUni(pointer);
            }
            finally
            {
                GlobalUnlock(handle);
            }
        }
        finally
        {
            CloseClipboard();
        }
    }

    /// <summary>截取主屏幕，调用方负责释放返回的位图</summary>
    public static Bitmap CaptureScreen()
    {
        int width = GetSystemMetrics(0);
        int height = GetSystemMetrics(1);
        Bitmap bitmap = new(width, height, PixelFormat.Format32bppArgb);
        try
        {
            using Graphics graphics = Graphics.FromImage(bitmap);
            graphics.CopyFromScreen(0, 0, 0, 0, new Size(width, height));
            return bitmap;
        }
        catch
        {
            bitmap.Dispose();
            throw;
        }
    }

    const uint CF_UNICODETEXT = 13;

    [StructLayout(LayoutKind.Sequential)]
    struct POINT
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct LASTINPUTINFO
    {
        public uint cbSize;
        public uint dwTime;
    }

    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int maxCount);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int virtualKey);
    [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO info);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")] static extern bool OpenClipboard(IntPtr newOwner);
    [DllImport("user32.dll")] static extern bool CloseClipboard();
    [DllImport("user32.dll")] static extern IntPtr GetClipboardData(uint format);
    [DllImport("kernel32.dll")] static extern IntPtr GlobalLock(IntPtr handle);
    [DllImport("kernel32.dll")] static extern bool GlobalUnlock(IntPtr handle);
}
