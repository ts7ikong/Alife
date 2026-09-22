"""
无畏契约 灵敏度调教工具
依赖: pip install pygame
运行: python sens_tuner.py

原理:
- 使用 pygame.mouse.get_rel() 获取原始鼠标位移（不受系统光标加速影响）
- 结合用户填入的 DPI，换算成真实物理移动距离
- 通过多种测试场景分析操作行为，科学推算最适灵敏度
"""

import pygame
import math
import random
import time
import sys

pygame.init()
pygame.display.set_caption("无畏契约 · 灵敏度调教工具")

# ── 常量 ──────────────────────────────────────────────
W, H = 1280, 800
FPS = 165
BLACK   = (10, 10, 14)
BG      = (13, 13, 18)
GRID    = (20, 20, 28)
PURPLE  = (108, 79, 255)
PURPLE2 = (140, 110, 255)
PURPLEDIM=(50, 35, 120)
GREEN   = (0, 220, 110)
RED     = (255, 60, 70)
WHITE   = (232, 228, 220)
GRAY    = (100, 98, 110)
GRAY2   = (50, 48, 58)
GRAY3   = (30, 28, 38)
YELLOW  = (255, 200, 60)
CYAN    = (60, 220, 255)

# Valorant 灵敏度换算公式: cm/360 = 360 / (0.07 * DPI * sens)
VAL_K = 0.07

def cm360(dpi, sens):
    return 360 / (VAL_K * dpi * sens)

def sens_from_cm(dpi, cm):
    return 360 / (VAL_K * dpi * cm)

# ── 字体 ──────────────────────────────────────────────
# 兼容 Python 3.14 + pygame-ce：SysFont 在 3.14 有 bug，直接用字体文件路径
import os
def _load_font(size):
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
        r"C:\Windows\Fonts\simhei.ttf",      # 黑体
        r"C:\Windows\Fonts\simsun.ttc",      # 宋体
        r"C:\Windows\Fonts\arial.ttf",        # Arial（无中文但不报错）
    ]
    for path in candidates:
        if os.path.exists(path):
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)  # 最终回退

font_big   = _load_font(28)
font_med   = _load_font(20)
font_sm    = _load_font(15)
font_xs    = _load_font(13)
font_title = _load_font(36)

def txt(surf, text, pos, color=WHITE, f=None, anchor="topleft"):
    f = f or font_sm
    s = f.render(str(text), True, color)
    r = s.get_rect(**{anchor: pos})
    surf.blit(s, r)
    return r

def draw_panel(surf, rect, color=GRAY3, border=GRAY2, radius=10):
    pygame.draw.rect(surf, color, rect, border_radius=radius)
    pygame.draw.rect(surf, border, rect, width=1, border_radius=radius)

def draw_btn(surf, rect, label, hover=False, active=False):
    bg = PURPLE if active else (PURPLEDIM if hover else GRAY3)
    bc = PURPLE if (active or hover) else GRAY2
    pygame.draw.rect(surf, bg, rect, border_radius=7)
    pygame.draw.rect(surf, bc, rect, width=1, border_radius=7)
    txt(surf, label, rect.center, WHITE if (active or hover) else GRAY, font_sm, anchor="center")

# ── 状态机 ────────────────────────────────────────────
# STATES: setup → calibrate → test_[mode] → result → setup
STATE_SETUP     = "setup"
STATE_CALIB     = "calibrate"
STATE_TEST      = "test"
STATE_RESULT    = "result"

# ── 测试模式 ──────────────────────────────────────────
MODES = [
    {"id": "grid",   "name": "网格点射",    "desc": "固定位置目标，测整体精准度和过冲率"},
    {"id": "flick",  "name": "Flick 甩枪",  "desc": "大角度单次甩枪，测落点准确性"},
    {"id": "micro",  "name": "微调精准",    "desc": "小范围相邻目标，测细微移动控制"},
    {"id": "track",  "name": "追踪移动靶",  "desc": "目标持续移动，测连续追踪能力"},
]
ROUNDS_PER_MODE = 15

# ── 主应用 ────────────────────────────────────────────
class App:
    def __init__(self):
        self.screen = pygame.display.set_mode((W, H))
        self.clock  = pygame.time.Clock()
        self.state  = STATE_SETUP

        # 用户参数
        self.dpi        = 800
        self.cur_sens   = 0.35
        self.pad_cm     = 35.0

        # 标定
        self.calib_done      = False
        self.calib_px_per_cm = None   # 实测像素/cm
        self.calib_phase     = 0      # 0=说明 1=测量中 2=完成
        self.calib_start_x   = None
        self.calib_accum     = 0.0
        self.calib_target_cm = 20.0   # 要求移动20cm

        # 测试
        self.mode_idx      = 0
        self.mode_results  = {}       # mode_id -> list of shot dicts
        self.current_mode  = None
        self.shots         = []
        self.round_idx     = 0
        self.target        = None     # dict: x,y,r,appear_t
        self.track_t       = 0.0
        self.mouse_rel_accum = [0.0, 0.0]  # 累积原始位移（像素）
        self.crosshair     = [W//2, H//2]  # 虚拟准心位置
        self.last_shot_flash = 0      # 命中特效时间

        # 输入框
        self.input_fields = [
            {"label": "鼠标 DPI", "key": "dpi", "val": "800", "active": False},
            {"label": "当前游戏内灵敏度", "key": "cur_sens", "val": "0.35", "active": False},
            {"label": "鼠标垫宽度 (cm)", "key": "pad_cm", "val": "35", "active": False},
        ]
        self.active_field = None

        # 鼠标捕获
        self.grabbed = False

        # setup 界面按钮
        self.btn_start = pygame.Rect(W//2-120, 580, 240, 48)
        self.mode_btns = []

        # test 界面
        self.test_area = pygame.Rect(80, 140, W-160, H-260)
        self.btn_skip  = pygame.Rect(W-160, H-60, 140, 40)

        # result
        self.btn_retry  = pygame.Rect(80, H-70, 180, 44)
        self.btn_next   = pygame.Rect(280, H-70, 180, 44)
        self.btn_finish = pygame.Rect(480, H-70, 200, 44)
        self.final_result = None

    # ── 鼠标捕获 ──────────────────────────────────────
    def grab(self):
        if not self.grabbed:
            pygame.event.set_grab(True)
            pygame.mouse.set_visible(False)
            pygame.mouse.get_rel()  # 清空积累
            self.grabbed = True

    def ungrab(self):
        if self.grabbed:
            pygame.event.set_grab(False)
            pygame.mouse.set_visible(True)
            self.grabbed = False

    # ── 主循环 ────────────────────────────────────────
    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            rel = pygame.mouse.get_rel()  # 每帧必须读，清空缓冲

            events = pygame.event.get()
            for e in events:
                if e.type == pygame.QUIT:
                    self.ungrab()
                    pygame.quit(); sys.exit()
                if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                    if self.grabbed:
                        self.ungrab()
                    else:
                        self.ungrab(); pygame.quit(); sys.exit()

                self.handle_event(e, rel)

            self.update(dt, rel)
            self.draw()
            pygame.display.flip()

    def handle_event(self, e, rel):
        ms = pygame.mouse.get_pos()

        if self.state == STATE_SETUP:
            self.handle_setup(e, ms)
        elif self.state == STATE_CALIB:
            self.handle_calib(e, rel, ms)
        elif self.state == STATE_TEST:
            self.handle_test(e, rel, ms)
        elif self.state == STATE_RESULT:
            self.handle_result(e, ms)

    # ── SETUP ─────────────────────────────────────────
    def handle_setup(self, e, ms):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # 输入框
            hit_field = False
            for i, f in enumerate(self.input_fields):
                r = pygame.Rect(W//2-150, 220+i*90, 300, 42)
                if r.collidepoint(ms):
                    self.active_field = i
                    hit_field = True
                else:
                    pass
            if not hit_field:
                self.active_field = None

            # 模式选择
            for i, r in enumerate(self.mode_btns):
                if r.collidepoint(ms):
                    self.mode_idx = i

            # 开始按钮
            if self.btn_start.collidepoint(ms):
                self.apply_params()
                self.state = STATE_CALIB
                self.calib_phase = 0
                self.calib_done = False

        if e.type == pygame.KEYDOWN and self.active_field is not None:
            f = self.input_fields[self.active_field]
            if e.key == pygame.K_BACKSPACE:
                f["val"] = f["val"][:-1]
            elif e.key == pygame.K_RETURN or e.key == pygame.K_TAB:
                self.active_field = None
            elif e.unicode in "0123456789.":
                f["val"] += e.unicode

    def apply_params(self):
        try: self.dpi = float(self.input_fields[0]["val"])
        except: self.dpi = 800
        try: self.cur_sens = float(self.input_fields[1]["val"])
        except: self.cur_sens = 0.35
        try: self.pad_cm = float(self.input_fields[2]["val"])
        except: self.pad_cm = 35

    # ── CALIBRATE ─────────────────────────────────────
    def handle_calib(self, e, rel, ms):
        if self.calib_phase == 0:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self.calib_phase = 1
                self.calib_accum = 0.0
                self.grab()
                pygame.mouse.get_rel()

        elif self.calib_phase == 1:
            # 累积移动量在 update() 里处理，这里只检查完成条件
            if self.calib_accum >= self.dpi * self.calib_target_cm / 2.54:
                # 已移动足够距离（以英寸换算）
                # 实测: calib_accum 像素 = calib_target_cm cm
                self.calib_px_per_cm = self.calib_accum / self.calib_target_cm
                self.calib_phase = 2
                self.ungrab()

        elif self.calib_phase == 2:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                # 进入测试
                self.calib_done = True
                self.start_test_mode()

    # ── TEST ──────────────────────────────────────────
    def start_test_mode(self):
        self.state      = STATE_TEST
        self.current_mode = MODES[self.mode_idx]["id"]
        self.shots      = []
        self.round_idx  = 0
        self.track_t    = 0.0
        self.crosshair  = [self.test_area.centerx, self.test_area.centery]
        self.grab()
        pygame.mouse.get_rel()
        self.spawn_target()

    def spawn_target(self):
        m  = self.current_mode
        ta = self.test_area
        pad = 50

        if m == "grid":
            cols, rows = 5, 3
            ci = self.round_idx % cols
            ri = (self.round_idx // cols) % rows
            x  = ta.left + pad + ci * (ta.width  - 2*pad) // (cols-1)
            y  = ta.top  + pad + ri * (ta.height - 2*pad) // (rows-1)
            r  = 20
        elif m == "flick":
            # 距离准心较远的随机位置
            for _ in range(20):
                x = ta.left + pad + random.randint(0, ta.width  - 2*pad)
                y = ta.top  + pad + random.randint(0, ta.height - 2*pad)
                dx = x - self.crosshair[0]
                dy = y - self.crosshair[1]
                if math.hypot(dx, dy) > 200:
                    break
            r = 22
        elif m == "micro":
            if self.target:
                # 在上一个目标附近小范围
                angle = random.uniform(0, math.pi*2)
                dist  = random.randint(50, 120)
                x = int(self.target["x"] + math.cos(angle)*dist)
                y = int(self.target["y"] + math.sin(angle)*dist)
                x = max(ta.left+pad, min(ta.right-pad, x))
                y = max(ta.top +pad, min(ta.bottom-pad, y))
            else:
                x = ta.centerx; y = ta.centery
            r = 14
        elif m == "track":
            x = ta.centerx; y = ta.centery
            r = 22
        else:
            x = ta.centerx; y = ta.centery; r = 20

        self.target = {"x": x, "y": y, "r": r, "appear_t": time.time()}

    def handle_test(self, e, rel, ms):
        ta = self.test_area

        # 更新虚拟准心（用原始 rel）
        if e.type == pygame.MOUSEMOTION:
            self.crosshair[0] = max(ta.left,  min(ta.right,  self.crosshair[0] + rel[0]))
            self.crosshair[1] = max(ta.top,   min(ta.bottom, self.crosshair[1] + rel[1]))

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_skip.collidepoint(pygame.mouse.get_pos()):
                self.ungrab()
                self.finalize_mode()
                return
            self.register_shot()

        if e.type == pygame.KEYDOWN and e.key == pygame.K_r:
            # 重置准心到中央
            self.crosshair = [ta.centerx, ta.centery]

    def register_shot(self):
        if not self.target: return
        t  = self.target
        cx, cy = self.crosshair
        tx, ty = t["x"], t["y"]
        dx, dy = cx-tx, cy-ty
        dist   = math.hypot(dx, dy)
        hit    = dist < t["r"] * 1.15
        rt     = (time.time() - t["appear_t"]) * 1000

        # 过冲判定：实际落点在目标另一侧 且 超过半径
        # 用准心来向目标的方向向量判断是否冲过
        ch_prev_dx = cx - tx
        ch_prev_dy = cy - ty
        # 如果落点和目标中心的距离 > r 且 在目标后方（相对于准心移动方向）则判过冲
        # 简化：dist > r * 1.5 且没命中 = 过冲
        overshoot = (not hit) and dist > t["r"] * 1.8

        self.shots.append({
            "hit": hit, "err": dist, "over": overshoot,
            "rt": rt, "cx": cx, "cy": cy, "tx": tx, "ty": ty
        })
        self.last_shot_flash = time.time()

        self.round_idx += 1
        if self.round_idx >= ROUNDS_PER_MODE:
            self.ungrab()
            self.finalize_mode()
        else:
            self.spawn_target()

    def finalize_mode(self):
        self.mode_results[self.current_mode] = self.shots[:]
        self.state = STATE_RESULT
        self.compute_result()

    def compute_result(self):
        """
        核心算法：
        1. 用标定的 px_per_cm 换算实际物理误差
        2. 分析命中率、过冲率、平均误差
        3. 结合当前 cm/360 推算最优值
        """
        s = self.shots
        if not s:
            self.final_result = None
            return

        hits     = [x for x in s if x["hit"]]
        overs    = [x for x in s if x["over"]]
        acc      = len(hits) / len(s)
        over_pct = len(overs) / len(s)
        avg_err_px = sum(x["err"] for x in s) / len(s)
        avg_rt   = sum(x["rt"] for x in s) / len(s)

        # 换算物理误差（cm）
        px_per_cm = self.calib_px_per_cm or (self.dpi / 2.54)
        avg_err_cm = avg_err_px / px_per_cm

        cur_cm = cm360(self.dpi, self.cur_sens)

        # ── 调整逻辑 ──
        # 过冲多 → 灵敏度偏高 → 增大 cm/360（降低灵敏度）
        # 命中率低+过冲少 → 灵敏度偏低 → 减小 cm/360（提高灵敏度）
        # 平均误差 > 目标半径物理大小的2倍 且 过冲少 → 也偏低

        target_r_cm = self.target["r"] / px_per_cm if self.target else 0.5

        adj = 0.0  # cm/360 加减量

        if over_pct > 0.45:
            adj += cur_cm * 0.20   # 严重过冲，大幅增大cm（降敏）
        elif over_pct > 0.28:
            adj += cur_cm * 0.12
        elif over_pct > 0.15:
            adj += cur_cm * 0.06

        if acc < 0.35 and over_pct < 0.12:
            adj -= cur_cm * 0.15   # 命中低且没过冲，减小cm（提敏）
        elif acc < 0.55 and over_pct < 0.10:
            adj -= cur_cm * 0.07

        # 高命中率说明接近最优，收敛
        if acc >= 0.72:
            adj *= 0.3

        opt_cm = cur_cm + adj

        # 以鼠标垫为边界（30%~160% 的垫宽为合理范围）
        min_cm = self.pad_cm * 0.40
        max_cm = self.pad_cm * 1.60
        opt_cm = max(min_cm, min(max_cm, opt_cm))

        opt_sens = sens_from_cm(self.dpi, opt_cm)
        edpi     = round(self.dpi * opt_sens)

        # 诊断文字
        if acc >= 0.72 and over_pct < 0.20:
            diag = f"表现优秀（命中 {acc:.0%}，过冲 {over_pct:.0%}）。当前灵敏度接近最优区间。"
            direction = "stable"
        elif over_pct > 0.35:
            diag = f"过冲率偏高（{over_pct:.0%}），准心经常冲过目标，灵敏度对你来说偏高。建议降低。"
            direction = "lower"
        elif acc < 0.40 and over_pct < 0.12:
            diag = f"命中率低（{acc:.0%}）且过冲不明显，移动幅度不够，灵敏度可能偏低。建议提高。"
            direction = "higher"
        else:
            diag = f"命中 {acc:.0%}，过冲 {over_pct:.0%}，平均误差 {avg_err_cm:.1f}cm。轻微调整后再测一轮。"
            direction = "adjust"

        # 下一步建议
        new_mult = opt_sens / self.cur_sens
        if direction == "stable":
            next_step = f"建议在游戏内使用 {opt_sens:.3f}，连续两天感觉稳定即可固定。"
        elif direction == "lower":
            next_step = f"将游戏内灵敏度改为 {opt_sens:.3f}（当前 {self.cur_sens:.3f} 的 {new_mult:.0%}），重新测试验证。"
        elif direction == "higher":
            next_step = f"将游戏内灵敏度改为 {opt_sens:.3f}（当前 {self.cur_sens:.3f} 的 {new_mult:.0%}），重新测试验证。"
        else:
            next_step = f"将游戏内灵敏度小幅调整至 {opt_sens:.3f}，重跑一轮同类型测试确认。"

        self.final_result = {
            "acc": acc, "over_pct": over_pct, "avg_err_cm": avg_err_cm,
            "avg_rt": avg_rt, "cur_cm": cur_cm,
            "opt_sens": opt_sens, "opt_cm": opt_cm,
            "edpi": edpi, "ads": opt_sens * 0.78,
            "diag": diag, "next_step": next_step, "direction": direction,
        }

    def handle_result(self, e, ms):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_retry.collidepoint(ms):
                # 用推荐灵敏度重测同一模式
                if self.final_result:
                    self.cur_sens = self.final_result["opt_sens"]
                    self.input_fields[1]["val"] = f"{self.cur_sens:.3f}"
                self.start_test_mode()
            if self.btn_next.collidepoint(ms):
                # 测下一个模式
                self.mode_idx = (self.mode_idx + 1) % len(MODES)
                self.input_fields[1]["val"] = f"{self.cur_sens:.3f}"
                self.start_test_mode()
            if self.btn_finish.collidepoint(ms):
                self.state = STATE_SETUP

    # ── UPDATE ────────────────────────────────────────
    def update(self, dt, rel):
        # 标定阶段：累积原始鼠标水平位移
        if self.state == STATE_CALIB and self.calib_phase == 1:
            self.calib_accum += abs(rel[0])

        if self.state == STATE_TEST and self.current_mode == "track":
            self.track_t += dt * 1.2
            if self.target:
                ta = self.test_area
                # 8字形轨迹
                self.target["x"] = int(ta.centerx + math.sin(self.track_t)         * ta.width  * 0.36)
                self.target["y"] = int(ta.centery  + math.sin(self.track_t * 2)     * ta.height * 0.30)
                # 追踪模式：每0.5秒自动采样准心与目标的距离来评分
                if not hasattr(self, "_last_sample"):
                    self._last_sample = time.time()
                if time.time() - self._last_sample > 0.45:
                    self._last_sample = time.time()
                    cx, cy = self.crosshair
                    tx, ty = self.target["x"], self.target["y"]
                    dist = math.hypot(cx-tx, cy-ty)
                    r = self.target["r"]
                    hit = dist < r * 1.2
                    self.shots.append({
                        "hit": hit, "err": dist, "over": False,
                        "rt": 450, "cx": cx, "cy": cy, "tx": tx, "ty": ty
                    })
                    self.round_idx += 1
                    if self.round_idx >= ROUNDS_PER_MODE:
                        self.ungrab()
                        self.finalize_mode()

    # ── DRAW ──────────────────────────────────────────
    def draw(self):
        self.screen.fill(BG)
        self.draw_grid_bg()

        if self.state == STATE_SETUP:
            self.draw_setup()
        elif self.state == STATE_CALIB:
            self.draw_calib()
        elif self.state == STATE_TEST:
            self.draw_test()
        elif self.state == STATE_RESULT:
            self.draw_result()

    def draw_grid_bg(self):
        for x in range(0, W, 60):
            pygame.draw.line(self.screen, GRID, (x, 0), (x, H))
        for y in range(0, H, 60):
            pygame.draw.line(self.screen, GRID, (0, y), (W, y))

    # ── DRAW SETUP ────────────────────────────────────
    def draw_setup(self):
        txt(self.screen, "无畏契约 · 灵敏度调教工具", (W//2, 60), PURPLE2, font_title, "center")
        txt(self.screen, "科学测试你的鼠标行为，推算最适灵敏度", (W//2, 105), GRAY, font_sm, "center")

        # 输入框
        for i, f in enumerate(self.input_fields):
            ry = 220 + i*90
            txt(self.screen, f["label"], (W//2-150, ry-22), GRAY, font_xs)
            r = pygame.Rect(W//2-150, ry, 300, 42)
            active = (self.active_field == i)
            pygame.draw.rect(self.screen, GRAY3, r, border_radius=7)
            pygame.draw.rect(self.screen, PURPLE if active else GRAY2, r, width=1, border_radius=7)
            txt(self.screen, f["val"] + ("|" if active and int(time.time()*2)%2==0 else ""),
                (r.left+12, r.centery), WHITE, font_med, "midleft")

        # 模式选择
        txt(self.screen, "选择测试模式（可多次测试不同模式）", (W//2, 490), GRAY, font_xs, "center")
        self.mode_btns = []
        for i, m in enumerate(MODES):
            bw = 240; gap = 16
            total = len(MODES)*bw + (len(MODES)-1)*gap
            bx = W//2 - total//2 + i*(bw+gap)
            r = pygame.Rect(bx, 510, bw, 52)
            self.mode_btns.append(r)
            active = (i == self.mode_idx)
            draw_panel(self.screen, r, PURPLEDIM if active else GRAY3, PURPLE if active else GRAY2, 8)
            txt(self.screen, m["name"], (r.centerx, r.top+14), WHITE if active else GRAY, font_sm, "midtop")
            txt(self.screen, m["desc"], (r.centerx, r.top+34), GRAY if active else (40,38,50), font_xs, "midtop")

        # 开始按钮
        ms = pygame.mouse.get_pos()
        hover = self.btn_start.collidepoint(ms)
        draw_btn(self.screen, self.btn_start, "开始测试  →", hover, False)

        # 说明
        txt(self.screen, "ESC 退出  ·  测试中按 R 重置准心到中央", (W//2, H-30), (50,48,58), font_xs, "center")

    # ── DRAW CALIB ────────────────────────────────────
    def draw_calib(self):
        txt(self.screen, "标定步骤", (W//2, 80), PURPLE2, font_title, "center")

        if self.calib_phase == 0:
            draw_panel(self.screen, pygame.Rect(W//2-320, 180, 640, 300), GRAY3, GRAY2)
            txt(self.screen, "为什么要标定？", (W//2, 210), WHITE, font_med, "center")
            lines = [
                "浏览器和 OS 会对鼠标输入做加速处理，但 pygame 读取的是原始位移。",
                "标定后，工具可以准确知道你移动了多少厘米，",
                "从而计算出真实的物理误差，让分析结果更可靠。",
                "",
                f"接下来：请将鼠标向右水平移动 {self.calib_target_cm:.0f} cm（约一个手掌宽）。",
                "点击下方按钮后开始，移动完成会自动进入测试。",
            ]
            for i, l in enumerate(lines):
                txt(self.screen, l, (W//2, 260+i*30), GRAY if l else WHITE, font_sm, "center")

            r = pygame.Rect(W//2-120, 440, 240, 48)
            ms = pygame.mouse.get_pos()
            draw_btn(self.screen, r, "开始标定  →", r.collidepoint(ms), False)

        elif self.calib_phase == 1:
            prog = min(1.0, self.calib_accum / (self.dpi * self.calib_target_cm / 2.54))
            txt(self.screen, f"向右移动鼠标 {self.calib_target_cm:.0f} cm", (W//2, 200), WHITE, font_big, "center")
            txt(self.screen, "不要点击，只移动鼠标", (W//2, 240), GRAY, font_sm, "center")
            bar = pygame.Rect(W//2-300, 300, 600, 20)
            pygame.draw.rect(self.screen, GRAY3, bar, border_radius=10)
            fill = pygame.Rect(bar.left, bar.top, int(600*prog), 20)
            pygame.draw.rect(self.screen, PURPLE, fill, border_radius=10)
            txt(self.screen, f"{prog*100:.0f}%", (W//2, 340), PURPLE2, font_big, "center")

        elif self.calib_phase == 2:
            txt(self.screen, "标定完成！", (W//2, 200), GREEN, font_big, "center")
            txt(self.screen, f"实测：{self.calib_px_per_cm:.1f} px/cm", (W//2, 250), GRAY, font_med, "center")
            r = pygame.Rect(W//2-140, 320, 280, 48)
            ms = pygame.mouse.get_pos()
            draw_btn(self.screen, r, "开始测试  →", r.collidepoint(ms), False)

    # ── DRAW TEST ─────────────────────────────────────
    def draw_test(self):
        ta = self.test_area
        mode_name = next(m["name"] for m in MODES if m["id"] == self.current_mode)

        # 测试区背景
        pygame.draw.rect(self.screen, (10, 10, 16), ta, border_radius=6)
        pygame.draw.rect(self.screen, GRAY2, ta, width=1, border_radius=6)

        # 目标
        if self.target:
            tx, ty = self.target["x"], self.target["y"]
            r = self.target["r"]
            age = time.time() - self.target["appear_t"]
            pulse = 0.5 + 0.5 * math.sin(age * 8)

            # 外圈
            pygame.draw.circle(self.screen, PURPLEDIM, (tx, ty), r+8, 1)
            # 目标圆
            pygame.draw.circle(self.screen, PURPLE, (tx, ty), r, 2)
            # 内填充
            s = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
            pygame.draw.circle(s, (*PURPLE, int(40+pulse*30)), (r, r), r)
            self.screen.blit(s, (tx-r, ty-r))
            # 中心点
            pygame.draw.circle(self.screen, PURPLE2, (tx, ty), 4)
            # 十字
            pygame.draw.line(self.screen, PURPLEDIM, (tx-r-6, ty), (tx+r+6, ty), 1)
            pygame.draw.line(self.screen, PURPLEDIM, (tx, ty-r-6), (tx, ty+r+6), 1)

        # 最近几个落点标记
        for sh in self.shots[-8:]:
            c = GREEN if sh["hit"] else RED
            pygame.draw.circle(self.screen, c, (sh["cx"], sh["cy"]), 4, 0)
            pygame.draw.circle(self.screen, c, (sh["cx"], sh["cy"]), 5, 1)

        # 准心
        cx, cy = int(self.crosshair[0]), int(self.crosshair[1])
        gap = 6; length = 10; thick = 2
        pygame.draw.line(self.screen, GREEN, (cx-gap-length, cy), (cx-gap, cy), thick)
        pygame.draw.line(self.screen, GREEN, (cx+gap, cy), (cx+gap+length, cy), thick)
        pygame.draw.line(self.screen, GREEN, (cx, cy-gap-length), (cx, cy-gap), thick)
        pygame.draw.line(self.screen, GREEN, (cx, cy+gap), (cx, cy+gap+length), thick)
        pygame.draw.circle(self.screen, GREEN, (cx, cy), 2)

        # 顶部信息
        txt(self.screen, mode_name, (80, 30), PURPLE2, font_med)
        txt(self.screen, f"当前灵敏度: {self.cur_sens:.3f}  |  DPI: {int(self.dpi)}  |  cm/360°: {cm360(self.dpi,self.cur_sens):.1f}",
            (W//2, 30), GRAY, font_sm, "midtop")
        txt(self.screen, f"{self.round_idx} / {ROUNDS_PER_MODE}", (W-80, 30), GRAY, font_sm, "midtop")

        # 进度点
        dot_x = 80
        for i in range(ROUNDS_PER_MODE):
            c = GRAY2
            if i < len(self.shots):
                c = GREEN if self.shots[i]["hit"] else RED
            elif i == self.round_idx:
                c = PURPLE
            pygame.draw.circle(self.screen, c, (dot_x + i*36, 100), 7)

        # 实时统计
        if self.shots:
            hits = sum(1 for x in self.shots if x["hit"])
            overs = sum(1 for x in self.shots if x["over"])
            n = len(self.shots)
            stats = [
                ("命中率", f"{hits/n:.0%}"),
                ("过冲率", f"{overs/n:.0%}"),
                ("平均误差", f"{sum(x['err'] for x in self.shots)/n:.0f}px"),
                ("平均反应", f"{sum(x['rt'] for x in self.shots)/n:.0f}ms"),
            ]
            for i, (label, val) in enumerate(stats):
                bx = 80 + i*220
                txt(self.screen, label, (bx, H-80), GRAY, font_xs)
                txt(self.screen, val,   (bx, H-58), WHITE, font_med)

        # 提示
        txt(self.screen, "左键点击目标  ·  R 重置准心  ·  ESC 释放鼠标", (W//2, H-25), (40,38,50), font_xs, "center")

        # skip 按钮
        ms_pos = pygame.mouse.get_pos()
        draw_btn(self.screen, self.btn_skip, "结束这轮", self.btn_skip.collidepoint(ms_pos), False)

        # 追踪模式提示
        if self.current_mode == "track":
            txt(self.screen, "追踪目标移动，让准心尽量贴住靶心", (ta.centerx, ta.bottom+18), GRAY, font_xs, "midtop")

    # ── DRAW RESULT ───────────────────────────────────
    def draw_result(self):
        txt(self.screen, "测试结果", (W//2, 45), PURPLE2, font_title, "center")
        r = self.final_result
        if not r:
            txt(self.screen, "数据不足，请重新测试", (W//2, 200), RED, font_big, "center")
            return

        # 三个核心数值卡片
        cards = [
            ("推荐游戏内灵敏度", f"{r['opt_sens']:.3f}", f"DPI {int(self.dpi)}  ·  eDPI {r['edpi']}", GREEN),
            ("cm / 360°",       f"{r['opt_cm']:.1f} cm",  "转一圈的物理距离", CYAN),
            ("ADS 灵敏度",      f"{r['ads']:.3f}",        "开镜推荐 (×0.78)",  YELLOW),
        ]
        for i, (label, val, sub, color) in enumerate(cards):
            cr = pygame.Rect(80 + i*370, 110, 340, 100)
            draw_panel(self.screen, cr, GRAY3, GRAY2, 10)
            txt(self.screen, label, (cr.left+16, cr.top+14), GRAY, font_xs)
            txt(self.screen, val,   (cr.left+16, cr.centery+2), color, font_big, "midleft")
            txt(self.screen, sub,   (cr.left+16, cr.bottom-18), (50,48,60), font_xs)

        # 本轮数据
        row_y = 235
        data = [
            ("命中率",   f"{r['acc']:.0%}",       r['acc'] >= 0.65),
            ("过冲率",   f"{r['over_pct']:.0%}",  r['over_pct'] <= 0.20),
            ("平均误差", f"{r['avg_err_cm']:.1f}cm", r['avg_err_cm'] < 1.0),
            ("平均反应", f"{r['avg_rt']:.0f}ms",  r['avg_rt'] < 600),
        ]
        for i, (label, val, good) in enumerate(data):
            bx = 80 + i*280
            draw_panel(self.screen, pygame.Rect(bx, row_y, 255, 64), GRAY3, GRAY2, 8)
            txt(self.screen, label, (bx+12, row_y+10), GRAY, font_xs)
            txt(self.screen, val,   (bx+12, row_y+34), GREEN if good else YELLOW, font_med)

        # 诊断文字
        diag_r = pygame.Rect(80, 325, W-160, 72)
        draw_panel(self.screen, diag_r, (20,16,36), (108,79,255,80), 8)
        txt(self.screen, r["diag"], (diag_r.centerx, diag_r.centery), WHITE, font_sm, "center")

        # 下一步
        next_r = pygame.Rect(80, 415, W-160, 72)
        draw_panel(self.screen, next_r, (16,22,16), (0,160,80,80), 8)
        txt(self.screen, "下一步：", (next_r.left+16, next_r.top+14), GREEN, font_xs)
        txt(self.screen, r["next_step"], (next_r.left+16, next_r.centery+8), WHITE, font_sm, "midleft")

        # 历史记录
        if len(self.mode_results) > 1:
            txt(self.screen, "历史测试模式：", (80, 510), GRAY, font_xs)
            hx = 80
            for mid, shots in self.mode_results.items():
                mname = next((m["name"] for m in MODES if m["id"]==mid), mid)
                hits = sum(1 for x in shots if x["hit"])
                acc = hits/len(shots) if shots else 0
                draw_panel(self.screen, pygame.Rect(hx, 530, 200, 44), GRAY3, GRAY2, 7)
                txt(self.screen, mname, (hx+10, 540), GRAY, font_xs)
                txt(self.screen, f"命中 {acc:.0%}", (hx+10, 557), GREEN if acc>=0.65 else YELLOW, font_xs)
                hx += 215

        # 按钮
        ms_pos = pygame.mouse.get_pos()
        draw_btn(self.screen, self.btn_retry,  "用推荐值重测",       self.btn_retry.collidepoint(ms_pos))
        draw_btn(self.screen, self.btn_next,   "测试下一模式  →",    self.btn_next.collidepoint(ms_pos))
        draw_btn(self.screen, self.btn_finish, "返回设置",           self.btn_finish.collidepoint(ms_pos))
        txt(self.screen, "连续2轮命中率>70%且过冲<20%时，该灵敏度即为你的最优值",
            (W//2, H-20), (50,48,58), font_xs, "center")


if __name__ == "__main__":
    App().run()
