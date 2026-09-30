using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Alife.Foundation;
using ElectronNET.API;
using ElectronNET.API.Entities;

namespace Alife.Function.PetActivity;

/// <param name="Label">按钮文字</param>
/// <param name="Kind">点击后提交给主进程的 kind；为空表示只关闭窗口</param>
public record PopupButton(string Label, string Kind);

/// <param name="CountdownSeconds">大于 0 时，倒计时结束后自动关闭窗口</param>
/// <param name="Centered">true 居中显示，false 显示在屏幕右下角</param>
public record PopupOptions(
    string Title,
    string Text,
    string Placeholder,
    string Hint,
    PopupButton[] Buttons,
    int CountdownSeconds,
    bool Centered,
    int Width = 340,
    int Height = 210);

/// <summary>
/// 带一个文本框和若干按钮的小弹窗。用户点击带 Kind 的按钮后，
/// 渲染进程通过 <see cref="SubmitChannel"/> 发送 {"kind":..., "text":...}。
/// </summary>
public static class PopupWindow
{
    public const string SubmitChannel = "petactivity:submit";

    public static async Task<BrowserWindow> ShowAsync(PopupOptions options)
    {
        string dir = Path.Combine(AlifePath.RuntimeFolderPath, "PetActivity");
        Directory.CreateDirectory(dir);
        string htmlPath = Path.Combine(dir, "popup.html");
        File.WriteAllText(htmlPath, BuildHtml(options), Encoding.UTF8);

        Display primary = await Electron.Screen.GetPrimaryDisplayAsync();
        int x, y;
        if (options.Centered)
        {
            x = primary.WorkArea.X + (primary.WorkArea.Width - options.Width) / 2;
            y = primary.WorkArea.Y + (primary.WorkArea.Height - options.Height) / 3;
        }
        else
        {
            x = primary.WorkArea.X + primary.WorkArea.Width - options.Width - 20;
            y = primary.WorkArea.Y + primary.WorkArea.Height - options.Height - 20;
        }

        BrowserWindow window = await Electron.WindowManager.CreateWindowAsync(new BrowserWindowOptions {
            Title = options.Title,
            X = x, Y = y, Width = options.Width, Height = options.Height,
            AlwaysOnTop = true,
            SkipTaskbar = true,
            Frame = false,
            Transparent = true,
            HasShadow = false,
            Show = false,
            Resizable = false,
            Fullscreenable = false,
            BackgroundColor = "#00000000",
            WebPreferences = new WebPreferences {
                NodeIntegration = true,
                ContextIsolation = false,
                Sandbox = false,
            }
        }, new Uri(htmlPath).AbsoluteUri);

        TaskCompletionSource ready = new();
        window.OnReadyToShow += () => ready.TrySetResult();
        await Task.WhenAny(ready.Task, Task.Delay(3000));

        window.Show();
        window.Focus();
        return window;
    }

    static string BuildHtml(PopupOptions options)
    {
        // 默认编码器会转义 < > & 与非 ASCII 字符，配置可以安全嵌入 <script>
        string config = JsonSerializer.Serialize(options, new JsonSerializerOptions {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase
        });
        return Template.Replace("/*CONFIG*/", config).Replace("/*CHANNEL*/", JsonSerializer.Serialize(SubmitChannel));
    }

    const string Template = """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
        <meta charset="UTF-8">
        <style>
          * { box-sizing: border-box; margin: 0; padding: 0; }
          html, body { height: 100%; background: transparent; }
          body { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; user-select: none; }
          .card {
            height: 100%; margin: 4px; padding: 12px 14px;
            background: #060810; border: 1px solid #0060cc; border-radius: 10px;
            display: flex; flex-direction: column; gap: 8px;
            -webkit-app-region: drag;
          }
          .title { color: #00c8ff; font-size: 13px; font-weight: bold; }
          textarea {
            flex: 1; resize: none; padding: 6px; font: 12px "Microsoft YaHei", sans-serif;
            color: #b0ccec; background: #030510; border: 1px solid #0e2050; border-radius: 5px;
            outline: none; -webkit-app-region: no-drag; user-select: text;
          }
          textarea:focus { border-color: #0060cc; }
          .row { display: flex; align-items: center; gap: 8px; -webkit-app-region: no-drag; }
          .hint { flex: 1; color: #4a6080; font-size: 11px; }
          button {
            padding: 5px 14px; font-size: 12px; cursor: pointer;
            color: #7eaacc; background: #050c1a; border: 1px solid #0e2050; border-radius: 5px;
          }
          button:hover { color: #00c8ff; background: #101828; border-color: #0060cc; }
          button.primary { color: #00c8ff; border-color: #0060cc; }
        </style>
        </head>
        <body>
        <div class="card">
          <div class="title" id="title"></div>
          <textarea id="input"></textarea>
          <div class="row">
            <span class="hint" id="hint"></span>
            <span id="buttons" class="row"></span>
          </div>
        </div>
        <script>
          const { ipcRenderer } = require('electron');
          const config = /*CONFIG*/;
          const channel = /*CHANNEL*/;
          const input = document.getElementById('input');
          const hint = document.getElementById('hint');

          document.getElementById('title').textContent = config.title;
          input.value = config.text;
          input.placeholder = config.placeholder;

          function submit(kind) {
            const text = input.value.trim();
            if (kind && text) ipcRenderer.send(channel, JSON.stringify({ kind, text }));
            window.close();
          }

          config.buttons.forEach((b, i) => {
            const el = document.createElement('button');
            el.textContent = b.label;
            if (i === 0) el.className = 'primary';
            el.onclick = () => submit(b.kind);
            document.getElementById('buttons').appendChild(el);
          });

          // Enter 触发第一个按钮，Ctrl+Enter 触发第二个按钮（若有），Esc 关闭
          input.addEventListener('keydown', e => {
            if (e.key === 'Escape') { window.close(); return; }
            if (e.key !== 'Enter' || e.shiftKey) return;
            e.preventDefault();
            const index = e.ctrlKey && config.buttons.length > 1 ? 1 : 0;
            submit(config.buttons[index].kind);
          });

          let remaining = config.countdownSeconds;
          function renderHint() {
            hint.textContent = remaining > 0 ? `${remaining} 秒后自动忽略` : config.hint;
          }
          renderHint();
          if (remaining > 0) {
            const timer = setInterval(() => {
              remaining -= 1;
              renderHint();
              if (remaining <= 0) { clearInterval(timer); window.close(); }
            }, 1000);
            // 用户开始编辑就取消自动关闭，避免写到一半窗口消失
            input.addEventListener('input', () => { clearInterval(timer); remaining = 0; renderHint(); }, { once: true });
          }
          input.focus();
        </script>
        </body>
        </html>
        """;
}
