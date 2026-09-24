using System;
using System.IO;
using System.Numerics;
using System.Threading.Tasks;
using Alife.Foundation;
using Alife.Framework;
using Alife.Function.DeskPet;
using ElectronNET.API;
using ElectronNET.API.Entities;

namespace Alife.Function.WorkKanban;

[Module("工作看板",
    "在桌宠上方（或屏幕右上角）显示当前时间、已工作时长和距下班时间的悬浮面板。",
    defaultCategory: "个人定制")]
public class WorkKanbanService(IDeskPet? deskPet = null) : ChatBehaviour, IConfigurable<WorkKanbanConfig>
{
    public WorkKanbanConfig Configuration { get; set; } = null!;

    BrowserWindow? window;
    readonly int w = 380, h = 178;
    float dpi = 1f;

    protected override async Task OnAwake()
    {
        string htmlDir = Path.Combine(AlifePath.RuntimeFolderPath, "WorkKanban");
        Directory.CreateDirectory(htmlDir);

        string htmlPath = Path.Combine(htmlDir, "index.html");
        File.WriteAllText(htmlPath, BuildHtml(Configuration.WorkSchedule));

        string url = new Uri(htmlPath).AbsoluteUri;

        Display primary = await Electron.Screen.GetPrimaryDisplayAsync();
        dpi = (float)primary.ScaleFactor;

        int x, y;
        if (deskPet != null)
        {
            Vector2 petPos = await deskPet.GetPosition();
            (x, y) = CalcPosition(petPos);
        }
        else
        {
            x = primary.WorkArea.X + primary.WorkArea.Width - w - 10;
            y = primary.WorkArea.Y + 10;
        }

        window = await Electron.WindowManager.CreateWindowAsync(new BrowserWindowOptions {
            Title = "工作看板",
            X = x, Y = y, Width = w, Height = h,
            AlwaysOnTop = true,
            SkipTaskbar = true,
            Frame = false,
            Transparent = true,
            HasShadow = false,
            Show = false,
            Resizable = false,
            Fullscreenable = false,
            BackgroundColor = "#00000000",
            Movable = true,
            WebPreferences = new WebPreferences {
                NodeIntegration = true,
                ContextIsolation = false,
                Sandbox = false,
            }
        }, url);

        TaskCompletionSource tcs = new();
        window.OnReadyToShow += () => tcs.TrySetResult();
        await Task.WhenAny(tcs.Task, Task.Delay(1000));

        window.SetAlwaysOnTop(true, (OnTopLevel)7, 1);
        window.Show();
    }

    protected override async Task OnUpdate()
    {
        if (deskPet == null || window == null) return;
        if (UpdateContext.FrameCount % 3 != 0) return; // 每 ~0.9s 更新一次位置

        Vector2 petPos = await deskPet.GetPosition();
        (int newX, int newY) = CalcPosition(petPos);
        window.SetBounds(new Rectangle { X = newX, Y = newY, Width = w, Height = h });
    }

    protected override Task OnDestroy()
    {
        try { window?.Destroy(); } catch { }
        window = null;
        return Task.CompletedTask;
    }

    // petPos 是 DPI 缩放后的物理像素中心坐标，转回逻辑像素后贴在桌宠正上方
    (int x, int y) CalcPosition(Vector2 petPos)
    {
        int petCenterX = (int)(petPos.X / dpi);
        int petCenterY = (int)(petPos.Y / dpi);
        // 250 ≈ 桌宠高度一半（默认 480px），10 为间距
        return (petCenterX - w / 2, petCenterY - 250 - h - 10);
    }

    static string BuildHtml(string workSchedule)
    {
        // 用 Replace 注入 schedule，避免 C# 字符串插值与 JS 花括号冲突
        const string template = """
            <!DOCTYPE html>
            <html>
            <head>
            <meta charset="utf-8">
            <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            html,body { width:100%; height:100%; background:transparent; overflow:hidden; }
            body {
                font-family:'Microsoft YaHei','Segoe UI',sans-serif;
                user-select:none;
                -webkit-app-region:drag;
                display:flex; align-items:center; justify-content:center;
            }
            .panel {
                width:calc(100% - 12px);
                padding:14px 20px 14px;
                background:linear-gradient(150deg, rgba(6,16,48,0.97) 0%, rgba(3,10,32,0.97) 100%);
                border-radius:18px;
                border:1.5px solid rgba(56,182,255,0.55);
                box-shadow:0 0 22px rgba(56,182,255,0.22), inset 0 0 18px rgba(56,182,255,0.04);
                color:white;
                display:flex; flex-direction:column; align-items:center; gap:8px;
            }
            .time {
                font-size:46px; font-weight:800; letter-spacing:4px;
                font-variant-numeric:tabular-nums;
                color:#38b6ff;
                text-shadow:0 0 18px rgba(56,182,255,0.9), 0 0 36px rgba(56,182,255,0.45);
                line-height:1;
            }
            .pill {
                width:100%; border-radius:22px; padding:6px 16px;
                background:rgba(14,32,72,0.85);
                border:1px solid rgba(56,182,255,0.18);
                font-size:14px; display:flex; align-items:center; justify-content:center; gap:7px;
            }
            .info-row { gap:10px; }
            .sep { color:rgba(255,255,255,0.2); font-size:16px; }
            </style>
            </head>
            <body>
            <div class="panel">
                <div class="time" id="T">--:--:--</div>
                <div class="pill" id="SP">💻 正常工作中...</div>
                <div class="pill info-row" id="IR" style="display:none">
                    <span>⏰</span><span id="worked">--</span>
                    <span class="sep">·</span>
                    <span>🏃</span><span id="cd">--</span>
                </div>
            </div>
            <script>
            const SCH = '__SCHEDULE__';
            function toMin(t){ const p=t.split(':'); return parseInt(p[0])*60+parseInt(p[1]); }
            function parseSch(s){ return s.split(',').map(seg=>{ const p=seg.trim().split('-'); return [toMin(p[0]),toMin(p[1])]; }); }
            function pad(n){ return String(n).padStart(2,'0'); }
            function fmtHM(m){ const h=Math.floor(m/60),n=m%60; return h>0?h+'h '+n+'m':n+'m'; }
            const ranges=parseSch(SCH);
            const dayStart=ranges[0][0], dayEnd=ranges[ranges.length-1][1];
            function update(){
                const now=new Date();
                const cur=now.getHours()*60+now.getMinutes();
                document.getElementById('T').textContent=pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds());
                let inWork=false,curEnd=null;
                for(const [s,e] of ranges){ if(cur>=s&&cur<e){ inWork=true;curEnd=e;break; } }
                const sp=document.getElementById('SP'), ir=document.getElementById('IR');
                if(inWork){
                    sp.innerHTML='💻 正常工作中...';
                    ir.style.display='flex';
                    document.getElementById('worked').textContent=fmtHM(cur-dayStart);
                    document.getElementById('cd').textContent=fmtHM(curEnd-cur)+' 后下班';
                } else if(cur>=dayEnd){
                    sp.innerHTML='🎉 已下班！';
                    ir.style.display='flex';
                    document.getElementById('worked').textContent='今日 '+fmtHM(dayEnd-dayStart);
                    document.getElementById('cd').textContent='收工啦';
                } else {
                    sp.innerHTML='☕ 工作未开始';
                    ir.style.display='none';
                }
            }
            setInterval(update,1000); update();
            </script>
            </body>
            </html>
            """;
        return template.Replace("__SCHEDULE__", workSchedule);
    }
}
