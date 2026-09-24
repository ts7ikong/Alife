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
    readonly int w = 215, h = 110;
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
            }
            .panel {
                margin:6px;
                padding:10px 14px;
                background:rgba(15,15,20,0.82);
                backdrop-filter:blur(16px);
                border-radius:14px;
                border:1px solid rgba(255,255,255,0.08);
                color:white;
            }
            .time { font-size:26px; font-weight:700; letter-spacing:2px; font-variant-numeric:tabular-nums; }
            .date { font-size:11px; color:rgba(255,255,255,0.45); margin-top:2px; }
            .row { margin-top:5px; font-size:12px; display:flex; align-items:center; gap:6px; }
            .tag { padding:2px 7px; border-radius:10px; font-size:10px; font-weight:600; }
            .work-tag { background:rgba(74,222,128,0.2); color:#4ade80; }
            .off-tag  { background:rgba(251,146,60,0.2);  color:#fb923c; }
            .val { font-variant-numeric:tabular-nums; }
            </style>
            </head>
            <body>
            <div class="panel">
                <div class="time" id="T">--:--</div>
                <div class="date" id="D">--</div>
                <div class="row" id="R1" style="display:none">
                    <span class="tag work-tag">工作中</span>
                    <span class="val" id="worked">--</span>
                </div>
                <div class="row" id="R2" style="display:none">
                    <span style="color:rgba(255,255,255,0.45)">距下班</span>
                    <span class="val" id="cd">--</span>
                </div>
                <div class="row" id="R3" style="display:none">
                    <span class="tag off-tag">已下班</span>
                    <span class="val" id="tot">--</span>
                </div>
            </div>
            <script>
            const SCH = '__SCHEDULE__';
            const WD = ['日','一','二','三','四','五','六'];
            function toMin(t){ const p=t.split(':'); return parseInt(p[0])*60+parseInt(p[1]); }
            function parseSch(s){ return s.split(',').map(seg=>{ const p=seg.trim().split('-'); return [toMin(p[0]),toMin(p[1])]; }); }
            function pad(n){ return String(n).padStart(2,'0'); }
            function fmt(m){ const h=Math.floor(m/60),n=m%60; return h>0?h+'h '+pad(n)+'m':n+'m'; }
            const ranges=parseSch(SCH);
            const dayStart=ranges[0][0], dayEnd=ranges[ranges.length-1][1];
            function update(){
                const now=new Date();
                const cur=now.getHours()*60+now.getMinutes();
                document.getElementById('T').textContent=pad(now.getHours())+':'+pad(now.getMinutes());
                document.getElementById('D').textContent=(now.getMonth()+1)+'月'+now.getDate()+'日 周'+WD[now.getDay()];
                let inWork=false,curEnd=null;
                for(const [s,e] of ranges){ if(cur>=s&&cur<e){ inWork=true;curEnd=e;break; } }
                const r1=document.getElementById('R1'),r2=document.getElementById('R2'),r3=document.getElementById('R3');
                if(inWork){
                    r1.style.display=r2.style.display='flex'; r3.style.display='none';
                    document.getElementById('worked').textContent=fmt(cur-dayStart);
                    document.getElementById('cd').textContent=fmt(curEnd-cur);
                } else if(cur>=dayEnd){
                    r1.style.display=r2.style.display='none'; r3.style.display='flex';
                    document.getElementById('tot').textContent='已工作 '+fmt(dayEnd-dayStart);
                } else {
                    r1.style.display=r2.style.display=r3.style.display='none';
                }
            }
            setInterval(update,5000); update();
            </script>
            </body>
            </html>
            """;
        return template.Replace("__SCHEDULE__", workSchedule);
    }
}
