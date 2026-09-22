"""
coin_engine.py — WorkingPet RPG 金币引擎

金币模型：
  - 每日基础金币 = 月薪 / 21.75（等值映射，不是真实货币）
  - 上班时间段内每秒积累金币
  - 活动监控检测到具体工作 → +50% 绩效金币
  - 摸鱼（鼠标键盘空闲）→ 基础产出 × 0.7，无绩效
  - 装备 / 天赋加成叠加在以上基础上
  - 累计历史金币驱动等级 / XP 系统

数据持久化到 coin_data.json（已加入 .gitignore）。
"""
import json
import os
from datetime import datetime

_BASE     = os.path.dirname(os.path.abspath(__file__))
COIN_FILE = os.path.join(_BASE, "coin_data.json")


# ── 等级公式 ─────────────────────────────────────────────────
# Level n → n+1 需要 n * 1000 金币
# Level 2 需累计 1000，Level 3 需累计 3000，Level 5 需 10000 ...
def level_from_coins(total: float) -> tuple[int, float, float]:
    """返回 (当前等级, 本级已积累, 本级所需总量)"""
    n = 1
    remaining = total
    while True:
        need = n * 1000.0
        if remaining < need:
            return n, remaining, need
        remaining -= need
        n += 1


# ── 装备图鉴 ─────────────────────────────────────────────────
EQUIPMENT_CATALOG: dict[str, dict] = {
    "copper_ring": {
        "name": "铜戒指", "icon": "💍", "cost": 200,
        "desc": "工作时金币产出 +5%",
        "effect": {"work_rate": 0.05},
    },
    "iron_boots": {
        "name": "铁靴", "icon": "👟", "cost": 500,
        "desc": "摸鱼时维持基础产出（不再 ×0.7）",
        "effect": {"idle_shield": True},
    },
    "lucky_charm": {
        "name": "幸运符", "icon": "🍀", "cost": 800,
        "desc": "基础产出 +10%",
        "effect": {"base_rate": 0.10},
    },
    "work_gloves": {
        "name": "工作手套", "icon": "🧤", "cost": 1500,
        "desc": "专注工作时产出 +20%",
        "effect": {"work_rate": 0.20},
    },
    "energy_drink": {
        "name": "能量饮料", "icon": "🥤", "cost": 2500,
        "desc": "全产出 +15%",
        "effect": {"all_rate": 0.15},
    },
    "crystal_crown": {
        "name": "水晶皇冠", "icon": "👑", "cost": 6000,
        "desc": "全产出 +30%，专属皇冠光环",
        "effect": {"all_rate": 0.30},
    },
}

# ── 天赋树 ────────────────────────────────────────────────────
TALENT_TREE: dict[str, dict] = {
    "coin_rate": {
        "name": "金币加速", "icon": "⚡",
        "desc": "每级全产出 +5%（上限 5 级）",
        "max_level": 5, "cost": 1,
        "effect_per_level": {"all_rate": 0.05},
    },
    "idle_shield": {
        "name": "摸鱼免疫", "icon": "🛡",
        "desc": "摸鱼时不再扣减（1 级满）",
        "max_level": 1, "cost": 2,
        "effect_per_level": {"idle_shield": True},
    },
    "bonus_amp": {
        "name": "绩效放大", "icon": "📈",
        "desc": "每级绩效加成 +10%（上限 3 级）",
        "max_level": 3, "cost": 2,
        "effect_per_level": {"bonus_mult": 0.10},
    },
    "focus_boost": {
        "name": "专注增幅", "icon": "🎯",
        "desc": "连续专注 30 min 后额外 +10%（1 级满）",
        "max_level": 1, "cost": 3,
        "effect_per_level": {"focus_boost": True},
    },
}


class CoinEngine:
    """
    每秒 tick，累计金币；持久化到 coin_data.json。
    所有调用均在 Qt 主线程（UI 定时器），不需要额外加锁。
    """
    MAX_EQUIP_SLOTS = 3

    def __init__(self, monthly_salary: float, work_seconds_per_day: float):
        self._daily_base = monthly_salary / 21.75
        self._work_sec   = max(work_seconds_per_day, 1.0)
        self._data       = self._load()
        self._today      = datetime.now().strftime("%Y-%m-%d")
        self._accum_frac = 0.0  # 小数部分，整数时触发 +1 动画
        self._focus_secs = 0    # 连续专注秒数

    # ── 持久化 ───────────────────────────────────────────────
    def _load(self) -> dict:
        data = {
            "date": "", "today_base": 0.0, "today_bonus": 0.0,
            # lifetime_xp 永远不扣除，用来计算等级；wallet 才是可消费余额。
            # 旧版本把 all_time 同时当经验和余额，购买装备会导致等级倒退。
            "lifetime_xp": 0.0, "wallet": 0.0, "spent_total": 0.0,
            "transactions": [], "talent_levels": {}, "equipment": [], "inventory": [],
        }
        if os.path.exists(COIN_FILE):
            try:
                loaded = json.loads(open(COIN_FILE, encoding="utf-8").read())
                data.update(loaded)
                # 无损兼容旧存档：旧 all_time 同时迁入经验和钱包。
                if "lifetime_xp" not in loaded:
                    legacy = float(loaded.get("all_time", 0.0))
                    data["lifetime_xp"] = legacy
                    data["wallet"] = legacy
                data.setdefault("transactions", [])
                data.setdefault("spent_total", 0.0)
                return data
            except Exception:
                pass
        return data

    def _credit(self, amount: float, reason: str):
        """入账同时增加永久经验，保留最近交易以便排查数值问题。"""
        if amount <= 0:
            return
        self._data["wallet"] = self._data.get("wallet", 0.0) + amount
        self._data["lifetime_xp"] = self._data.get("lifetime_xp", 0.0) + amount
        self._data.setdefault("transactions", []).append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "amount": round(amount, 4), "reason": reason,
        })
        self._data["transactions"] = self._data["transactions"][-100:]

    def _save(self):
        try:
            with open(COIN_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def save_periodic(self):
        self._save()

    def initialize_day(self, elapsed_work_seconds: float):
        """
        启动时调用一次，按时钟位置补齐今日基础金币。
        例如 16:20 启动，自动补入已过去 6h20m 对应的基础金币，
        不必从0开始。关闭后重开也会自动补上离线期间的差值。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        base_from_clock = self._daily_base * min(elapsed_work_seconds, self._work_sec) / self._work_sec

        if self._data.get("date") != today:
            # 新的一天：all_time 保留，重置今日字段并按时钟补基础金币
            self._data["date"]        = today
            self._data["today_base"]  = base_from_clock
            self._data["today_bonus"] = 0.0
            self._credit(base_from_clock, "今日基础金币补齐")
        else:
            # 同一天重新打开：补上离线期间未计入的基础金币
            stored_base = self._data.get("today_base", 0.0)
            if base_from_clock > stored_base:
                gap = base_from_clock - stored_base
                self._data["today_base"] = base_from_clock
                self._credit(gap, "离线基础金币补齐")

        self._today = today
        self._save()

    # ── 参数更新（设置变更时调用）────────────────────────────
    def update_salary(self, monthly_salary: float, work_seconds_per_day: float):
        self._daily_base = monthly_salary / 21.75
        self._work_sec   = max(work_seconds_per_day, 1.0)

    # ── 装备效果汇总 ─────────────────────────────────────────
    def _get_effects(self) -> dict:
        fx: dict = {
            "all_rate": 0.0, "base_rate": 0.0, "work_rate": 0.0,
            "idle_shield": False, "bonus_mult": 0.0, "focus_boost": False,
        }
        for tid, info in TALENT_TREE.items():
            lvl = self._data.get("talent_levels", {}).get(tid, 0)
            if lvl == 0:
                continue
            for k, v in info["effect_per_level"].items():
                if isinstance(v, bool):
                    fx[k] = fx[k] or (v and lvl > 0)
                else:
                    fx[k] = fx.get(k, 0.0) + v * lvl
        for eid in self._data.get("equipment", []):
            eq = EQUIPMENT_CATALOG.get(eid, {})
            for k, v in eq.get("effect", {}).items():
                if isinstance(v, bool):
                    fx[k] = fx[k] or v
                else:
                    fx[k] = fx.get(k, 0.0) + v
        return fx

    # ── 每秒 tick ─────────────────────────────────────────────
    def tick(self, is_work_hour: bool, is_working: bool, is_idle: bool) -> int:
        """
        每秒调用，返回本次增加的整数金币数（0 或 1，极少为 2+）。
        调用方用返回值决定是否触发 +1 浮动动画。
        """
        if not is_work_hour:
            self._focus_secs = 0
            return 0

        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today:
            # 跨午夜：新的一天从0开始（上班前）
            self._today               = today
            self._data["date"]        = today
            self._data["today_base"]  = 0.0
            self._data["today_bonus"] = 0.0

        fx = self._get_effects()

        base_rate = (self._daily_base / self._work_sec) * (1 + fx["base_rate"] + fx["all_rate"])

        if is_idle and not fx["idle_shield"]:
            base_rate *= 0.70
            bonus_rate = 0.0
            self._focus_secs = 0
        elif is_working:
            bonus_mult = 0.50 + fx["bonus_mult"]
            if fx["focus_boost"] and self._focus_secs >= 1800:
                bonus_mult += 0.10
            bonus_rate = base_rate * (bonus_mult + fx["work_rate"])
            self._focus_secs += 1
        else:
            bonus_rate = 0.0
            self._focus_secs = 0

        earned = base_rate + bonus_rate
        self._data["today_base"]  = self._data.get("today_base",  0.0) + base_rate
        self._data["today_bonus"] = self._data.get("today_bonus", 0.0) + bonus_rate
        self._credit(earned, "工作产出")

        self._accum_frac += earned
        whole = int(self._accum_frac)
        self._accum_frac -= whole
        return whole

    # ── 展示数据 ─────────────────────────────────────────────
    def get_display(self) -> dict:
        lifetime_xp = self._data.get("lifetime_xp", 0.0)
        today_base  = self._data.get("today_base",  0.0)
        today_bonus = self._data.get("today_bonus", 0.0)
        level, xp, xp_next = level_from_coins(lifetime_xp)
        pts = self._available_talent_points(level)
        return {
            "today_base":  today_base,
            "today_bonus": today_bonus,
            "today_total": today_base + today_bonus,
            "lifetime_xp": lifetime_xp,
            "wallet":      self._data.get("wallet", 0.0),
            "level":       level,
            "xp":          xp,
            "xp_next":     xp_next,
            "xp_pct":      min(100, int(xp / max(xp_next, 1) * 100)),
            "talent_pts":  pts,
        }

    def _available_talent_points(self, level: int) -> int:
        earned = level - 1
        used   = sum(self._data.get("talent_levels", {}).values())
        return max(0, earned - used)

    def get_talent_levels(self) -> dict:
        return dict(self._data.get("talent_levels", {}))

    def get_equipment(self) -> list:
        return list(self._data.get("equipment", []))

    def get_inventory(self) -> list:
        return list(self._data.get("inventory", []))

    def get_all_time(self) -> float:
        """兼容旧 UI 名称；返回可消费余额。"""
        return self._data.get("wallet", 0.0)

    # ── 商店 ─────────────────────────────────────────────────
    def can_buy(self, item_id: str) -> tuple[bool, str]:
        eq = EQUIPMENT_CATALOG.get(item_id)
        if not eq:
            return False, "未知道具"
        owned = (self._data.get("inventory", []) + self._data.get("equipment", []))
        if item_id in owned:
            return False, "已拥有"
        if self._data.get("wallet", 0.0) < eq["cost"]:
            return False, f"金币不足（需 {eq['cost']}🪙）"
        return True, ""

    def buy(self, item_id: str) -> tuple[bool, str]:
        ok, msg = self.can_buy(item_id)
        if not ok:
            return False, msg
        eq = EQUIPMENT_CATALOG[item_id]
        self._data["wallet"] = self._data.get("wallet", 0.0) - eq["cost"]
        self._data["spent_total"] = self._data.get("spent_total", 0.0) + eq["cost"]
        self._data.setdefault("transactions", []).append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "amount": -eq["cost"], "reason": f"购买 {eq['name']}",
        })
        self._data["transactions"] = self._data["transactions"][-100:]
        self._data.setdefault("inventory", []).append(item_id)
        self._save()
        return True, f"购入 {eq['name']}！"

    def equip(self, item_id: str) -> tuple[bool, str]:
        inv      = self._data.get("inventory", [])
        equipped = self._data.get("equipment", [])
        if item_id not in inv:
            return False, "背包里没有这件"
        if item_id in equipped:
            return False, "已装备"
        if len(equipped) >= self.MAX_EQUIP_SLOTS:
            return False, f"最多装备 {self.MAX_EQUIP_SLOTS} 件"
        inv.remove(item_id)
        equipped.append(item_id)
        self._data["inventory"] = inv
        self._data["equipment"] = equipped
        self._save()
        return True, "装备成功"

    def unequip(self, item_id: str) -> tuple[bool, str]:
        equipped = self._data.get("equipment", [])
        if item_id not in equipped:
            return False, "未装备"
        equipped.remove(item_id)
        self._data.setdefault("inventory", []).append(item_id)
        self._data["equipment"] = equipped
        self._save()
        return True, "已卸下"

    # ── 天赋树 ───────────────────────────────────────────────
    def spend_talent_point(self, talent_id: str) -> tuple[bool, str]:
        info = TALENT_TREE.get(talent_id)
        if not info:
            return False, "未知天赋"
        levels = self._data.setdefault("talent_levels", {})
        cur = levels.get(talent_id, 0)
        if cur >= info["max_level"]:
            return False, "已满级"
        d = self.get_display()
        if d["talent_pts"] < info["cost"]:
            return False, f"天赋点不足（需 {info['cost']}）"
        levels[talent_id] = cur + 1
        self._save()
        return True, f"{info['name']} Lv.{cur+1}！"
