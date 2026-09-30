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
public class WorkKanbanService : ChatBehaviour, IConfigurable<WorkKanbanConfig>
{
    public WorkKanbanConfig Configuration { get; set; } = null!;

    BrowserWindow? window;
    IDeskPet? deskPet;
    readonly int w = 366, h = 190;
    float dpi = 1f;

    protected override async Task OnAwake()
    {
        // 在所有模块构造完成后再查找 IDeskPet，避免构造顺序依赖
        foreach (object inst in ChatActivity.Container.Instances)
            if (inst is IDeskPet pet) { deskPet = pet; break; }
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
            <html lang="zh-CN">
            <head>
            <meta charset="UTF-8">
            <style>
              :root{
                --panel:#050b18;
                --line:#0b4f83;
                --cyan:#19d9ff;
                --text:#e8f4ff;
                --muted:#8192a8;
              }
              *{box-sizing:border-box;margin:0;padding:0}
              body{
                min-height:100vh;
                display:flex;align-items:center;justify-content:center;
                background:transparent;
                font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;
                color:var(--text);
                user-select:none;
                -webkit-app-region:drag;
                overflow:hidden;
              }
              .widget{
                width:350px;
                min-height:170px;
                padding:12px 16px 13px;
                border:1px solid #0b4673;
                border-radius:15px;
                background:linear-gradient(180deg,rgba(5,12,27,.97),rgba(2,7,17,.98));
                box-shadow:0 0 22px rgba(0,120,210,.10),inset 0 0 18px rgba(0,120,210,.035);
                position:relative;overflow:hidden;
              }
              .widget::before{
                content:"";position:absolute;left:0;right:0;top:0;height:1px;
                background:linear-gradient(90deg,transparent,#11bfff,transparent);opacity:.75;
              }
              .clock{
                text-align:center;font-size:25px;line-height:29px;
                letter-spacing:2px;font-weight:700;color:var(--cyan);
                text-shadow:0 0 9px rgba(0,217,255,.42);
              }
              .status{
                margin:2px auto 8px;width:max-content;color:#91a5ba;
                font-size:11px;display:flex;align-items:center;gap:4px;
              }
              .status-dot{
                width:6px;height:6px;border-radius:50%;
                background:#39c9ff;box-shadow:0 0 7px #39c9ff;
              }
              .info{
                height:31px;border-radius:3px;
                background:linear-gradient(90deg,#061528,#081a2e,#061528);
                display:flex;align-items:center;justify-content:center;
                gap:17px;color:#8798ad;font-size:11px;
              }
              .info span{display:flex;align-items:center;gap:5px;white-space:nowrap;}
              .divider{width:1px;height:13px;background:#20354b;}
              .progress{margin-top:10px;}
              .progress-track{height:2px;background:#102b43;border-radius:4px;overflow:hidden;}
              .progress-fill{
                width:0%;height:100%;
                background:linear-gradient(90deg,#0dafff,#3ee8ff);
                box-shadow:0 0 7px rgba(28,218,255,.65);
                transition:width .5s;
              }
              .time-row{
                margin-top:5px;display:flex;justify-content:space-between;
                align-items:center;color:#6f8499;font-size:10px;
              }
              .time-row strong{color:#b9c9d9;font-weight:500;}
              .time-row .cyan{color:#32d8ff;}
              .drag-handle{
                position:absolute;top:6px;left:50%;transform:translateX(-50%);
                width:28px;height:3px;border-radius:3px;background:#15324c;opacity:.5;
              }
            </style>
            </head>
            <body>
              <div class="widget">
                <div class="drag-handle"></div>
                <div class="clock" id="clock">--:--:--</div>
                <div class="status">
                  <span class="status-dot" id="dot"></span>
                  <span id="statusText">--</span>
                </div>
                <div class="info">
                  <span>⏰ <b id="worked">--</b></span>
                  <span class="divider"></span>
                  <span>🏃 <b id="remaining">--</b></span>
                </div>
                <div class="progress">
                  <div class="progress-track">
                    <div class="progress-fill" id="progressFill"></div>
                  </div>
                  <div class="time-row">
                    <span>今日工作进度</span>
                    <span><strong class="cyan" id="percent">0%</strong></span>
                  </div>
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
              const totalMin=dayEnd-dayStart;
              function update(){
                const now=new Date();
                const cur=now.getHours()*60+now.getMinutes();
                document.getElementById('clock').textContent=pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds());
                let inWork=false,curEnd=null;
                for(const [s,e] of ranges){ if(cur>=s&&cur<e){ inWork=true;curEnd=e;break; } }
                const workedMin=Math.max(0,Math.min(cur-dayStart,totalMin));
                const pct=totalMin>0?Math.round(workedMin/totalMin*100):0;
                document.getElementById('progressFill').style.width=pct+'%';
                document.getElementById('percent').textContent=pct+'%';
                document.getElementById('worked').textContent=fmtHM(workedMin);
                if(inWork){
                  document.getElementById('statusText').textContent='正常工作中...';
                  document.getElementById('dot').style.background='#39c9ff';
                  document.getElementById('remaining').textContent=fmtHM(curEnd-cur)+' 后下班';
                } else if(cur>=dayEnd){
                  document.getElementById('statusText').textContent='已下班 🎉';
                  document.getElementById('dot').style.background='#4ade80';
                  document.getElementById('remaining').textContent='收工啦';
                } else {
                  document.getElementById('statusText').textContent='工作未开始';
                  document.getElementById('dot').style.background='#fb923c';
                  document.getElementById('remaining').textContent='--';
                }
              }
              update(); setInterval(update,1000);
            </script>
            </body>
            </html>
            """;
        return template.Replace("__SCHEDULE__", workSchedule);
    }
}
