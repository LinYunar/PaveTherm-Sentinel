#!/usr/bin/env python3
"""
alert.py - 4 级预警判定 + 飞书卡片渲染 (v1 schema 优化版)

飞书 v1 卡片元素用法:
  - header: 顶部色条 + 标题 (template 决定颜色: green/yellow/orange/red/blue/...)
  - div: 单行文本块 (支持 icon 字段)
  - column_set + column: 多列复杂布局 (v1 唯一的多列元素)
  - markdown: 完整 markdown 渲染 (表格/引用/链接)
  - hr: 分隔线
  - note: 底部灰色注释
  - action: 按钮
  ⚠️ fields 元素是 v2 才有, v1 不可用 (11310 unsupported type of block)

排版策略 (优化版):
  1. header: 颜色条 + 标题 (用 emoji + 简短名)
  2. info div: 监测点元信息 (位置/坐标/路面)
  3. column_set 2x2: 当前数据 (路表/气温/辐射/置信度)
  4. alert div: 预警等级 + 建议 (大字号,带 icon)
  5. div+column_set: 14 天表
  6. action: "查看完整数据" / "切换至批量模式"
  7. note: 数据源 + 模型版本

4 级阈值 (默认,可在 config.yaml 修改;每个监测点可单独覆盖):
  green  < 55 °C
  yellow 55 ~ 60 °C
  orange 60 ~ 65 °C
  red    >= 65 °C
"""
import yaml
from pathlib import Path
from datetime import datetime

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.yaml"

# 委托给统一配置加载器
try:
    from config_loader import load_config as _load_config
except ImportError:
    _load_config = None


def load_alert_config() -> dict:
    if _load_config is not None:
        return _load_config(strict=False).get("alert", {})
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f).get("alert", {})


def classify_level(pavement_temp: float, thresholds: dict | None = None) -> dict:
    if thresholds is None:
        cfg = load_alert_config()
        thresholds = cfg.get("default_thresholds", {"yellow": 55, "orange": 60, "red": 65})

    yellow_t = thresholds.get("yellow", 55)
    orange_t = thresholds.get("orange", 60)
    red_t = thresholds.get("red", 65)

    cfg = load_alert_config()
    levels = cfg.get("levels", {})

    if pavement_temp >= red_t:
        level = "red"
    elif pavement_temp >= orange_t:
        level = "orange"
    elif pavement_temp >= yellow_t:
        level = "yellow"
    else:
        level = "green"

    info = levels.get(level, {})
    return {
        "level": level,
        "label": info.get("label", level),
        "advice": info.get("advice", ""),
        "pavement_temp": pavement_temp,
    }


def temp_to_dual(celsius: float) -> str:
    fahrenheit = celsius * 9 / 5 + 32
    return f"{celsius:.1f}°C / {fahrenheit:.1f}°F"


# ====== 飞书 v1 卡片颜色映射 ======
LEVEL_TO_TEMPLATE = {
    "green": "green",
    "yellow": "yellow",
    "orange": "orange",
    "red": "red",
}
LEVEL_TO_EMOJI = {
    "green": "✅",
    "yellow": "⚠️",
    "orange": "🟠",
    "red": "🔴",
}
LEVEL_TO_TAG_COLOR = {
    "green": "green",
    "yellow": "yellow",
    "orange": "orange",
    "red": "red",
}


def _tag(content: str, color: str = "neutral") -> dict:
    """飞书 v1 标签元素 (v1.0 不支持, 用 plain_text 替代做简易版)"""
    return {"tag": "plain_text", "content": f"[{content}]"}


def render_feishu_card(point: dict, current: dict, daily_peaks: list, alert_summary: dict) -> dict:
    """
    渲染飞书 v1 卡片 - 优化排版版。

    卡片结构:
      1. header (颜色条 + 标题)
      2. 监测点信息 (div)
      3. 当前数据 (fields 2x2)
      4. 预警等级 (div,大字,带 icon)
      5. 14 天峰值表 (markdown, 完整版)
      6. 高风险日 (fields 横向 3 列)
      7. hr
      8. note 数据源
    """
    cur_pt = current["pavement_temp"]
    cur_at = current["air_temp"]
    level = alert_summary["level"]
    template = LEVEL_TO_TEMPLATE.get(level, "grey")
    emoji = LEVEL_TO_EMOJI[level]
    confidence = current.get("confidence", "?")

    # ====== 构造 14 天 markdown 表 (单点) ======
    days_lines = ["| 日期 | 等级 | 峰值 (°C/F) | 时间 |", "|------|------|-------------|------|"]
    for p in daily_peaks[:14]:
        lvl = classify_level(p["peak_pavement_temp"])["level"]
        e = LEVEL_TO_EMOJI[lvl]
        c = p["peak_pavement_temp"]
        f = c * 9 / 5 + 32
        days_lines.append(f"| {p['date']} | {e} | **{c:.1f}°C / {f:.1f}°F** | {p['peak_time'][11:16]} |")
    days_md = "\n".join(days_lines)

    # ====== 高风险日 (只显示橙红) ======
    high_risk = [p for p in daily_peaks if classify_level(p["peak_pavement_temp"])["level"] in ("orange", "red")]

    # ====== 构造 cards 元素 ======
    elements = []

    # 1. 监测点元信息 (div + fields)
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📍 位置**  {point.get('geocode_display') or point.get('location_input', '?')}\n"
                       f"**🌐 坐标**  `{point.get('lat', '?')}, {point.get('lon', '?')}`\n"
                       f"**🛣️ 路面**  {point.get('pavement_color', 'unknown')} · {point.get('pavement_type') or '?'} · "
                       f"老化 {point.get('pavement_age_years') or '未知'}年"
        }
    })

    # 2. 当前数据 (2x2 column_set)
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📊 当前路表温度** ({datetime.now().strftime('%m-%d %H:%M')})"
        }
    })
    elements.append({
        "tag": "column_set",
        "flex_mode": "stretch",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**路表温度**\n**{temp_to_dual(cur_pt)}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**气温**\n{temp_to_dual(cur_at)}"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**太阳辐射**\n{current.get('gti_w_m2', 0):.0f} W/m²"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**模型置信度**\n`{confidence}`"}}
            ]},
        ]
    })

    # 3. 预警等级 (大 div)
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"## {emoji} 预警: **{alert_summary['label']}**\n"
                       f"> 💡 {alert_summary['advice']}"
        }
    })

    # 4. 14 天峰值表 (markdown 完整版)
    elements.append({"tag": "hr"})
    if len(daily_peaks) >= 14:
        title_md = f"**📅 未来 14 天路表温度峰值** (按日 11:00-16:00 窗口)"
    else:
        first = daily_peaks[0]["date"]
        last = daily_peaks[-1]["date"]
        title_md = f"**📅 未来 {len(daily_peaks)} 天路表温度峰值** ({first} 至 {last}, 按日 11:00-16:00 窗口)"
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": title_md
        }
    })
    elements.append({
        "tag": "markdown",
        "content": days_md
    })

    # 5. 高风险日 (column_set 横向, 3 列)
    if high_risk:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🔥 高风险日 (橙/红预警): {len(high_risk)} 天**"
            }
        })
        # 按 3 列分组展示 (column_set 一行多列)
        columns = []
        for d in high_risk[:9]:
            lvl = classify_level(d["peak_pavement_temp"])["level"]
            e = LEVEL_TO_EMOJI[lvl]
            c = d["peak_pavement_temp"]
            f = c * 9 / 5 + 32
            columns.append({
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**{d['date']}** {e}\n**{c:.1f}°C / {f:.1f}°F**\n峰值 {d['peak_time'][11:16]}"}}
                ]
            })
        if columns:
            elements.append({
                "tag": "column_set",
                "flex_mode": "stretch",
                "columns": columns
            })

    # 6. footer note
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": f"🕐 数据拉取: {point.get('last_query', '-')[:19] if point.get('last_query') else '本次实时'}"},
            {"tag": "plain_text", "content": "🧮 模型: SHRP/LTPP 经验公式 v0.1 | 📡 数据源: Open-Meteo + 高德地图"}
        ]
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {
                    "tag": "plain_text",
                    "content": f"🌡️ {point['name']} - 路表温度监测"
                }
            },
            "elements": elements
        }
    }


def render_multi_point_card(points_alerts: list) -> dict:
    """批量: 多监测点摘要卡片 - 完整版 (含时间+未来预测+位置)"""
    if not points_alerts:
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"template": "grey", "title": {"tag": "plain_text", "content": "PaveTherm Sentinel"}},
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "⚠️ 暂无监测点数据"}}],
            }
        }

    level_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_pts = sorted(
        points_alerts,
        key=lambda x: (level_order.get(x["alert_summary"]["level"], 9), -x["current_temp"])
    )
    top_level = sorted_pts[0]["alert_summary"]["level"]
    template = LEVEL_TO_TEMPLATE.get(top_level, "grey")
    top_emoji = LEVEL_TO_EMOJI[top_level]

    # ====== 统计 ======
    counts = {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    for x in points_alerts:
        counts[x["alert_summary"]["level"]] += 1

    # 14 天内全网最危险 (任意点的 peak 最高那天)
    global_peak = max(points_alerts, key=lambda x: x.get("peak_14d_max", 0))
    global_peak_level = classify_level(global_peak.get("peak_14d_max", 0))["level"]

    elements = []

    # 1. 摘要 + 4 列统计
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📡 共 {len(points_alerts)} 个监测点** · "
                       f"按预警等级 + 路表温度降序"
        }
    })
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "column_set",
        "flex_mode": "stretch",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**🔴 红警**\n**{counts['red']}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**🟠 橙警**\n**{counts['orange']}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**🟡 黄警**\n**{counts['yellow']}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**🟢 绿警**\n**{counts['green']}**"}}
            ]},
        ]
    })

    # 2. 14 天全网最危险日 (跨点聚合)
    if global_peak.get("peak_14d_max_date"):
        elements.append({"tag": "hr"})
        gpd_date = global_peak["peak_14d_max_date"]
        gpd_time = global_peak.get("peak_14d_max_time", "??:??")
        gpd_temp = global_peak.get("peak_14d_max", 0)
        gpd_level = classify_level(gpd_temp)["level"]
        gpd_emoji = LEVEL_TO_EMOJI[gpd_level]
        gpd_point_name = global_peak["point"]["name"]
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**📅 14 天内全网最危险日**: "
                           f"**{gpd_date}** @ **{gpd_time}** "
                           f"\n{gpd_emoji} **{gpd_point_name}** → **{gpd_temp:.1f}°C** ({classify_level(gpd_temp)['label']})"
            }
        })

    elements.append({"tag": "hr"})

    # 3. 每个监测点 1 个 column (2 列网格), 含: 位置/当前+时间/14天峰值+时间
    # 飞书 column_set 限制 1 行最多 N 列, 分组: 每 2 个点 1 行
    POINT_PER_ROW = 2
    for i in range(0, len(sorted_pts), POINT_PER_ROW):
        row_pts = sorted_pts[i:i + POINT_PER_ROW]
        columns = []
        for x in row_pts:
            p = x["point"]
            a = x["alert_summary"]
            emoji = LEVEL_TO_EMOJI[a["level"]]
            cur_t = x["current_temp"]
            cur_air = x.get("current_air_temp", "?")
            cur_time_short = (x.get("current_time") or "")[11:16] if x.get("current_time") else "??:??"
            peak_max = x.get("peak_14d_max", 0)
            peak_max_date = x.get("peak_14d_max_date", "?")
            peak_max_time = x.get("peak_14d_max_time", "??:??")
            peak_max_air = x.get("peak_14d_max_air", "?")
            location = p.get("geocode_display") or p.get("location_input", "")
            # 缩短地址 (取区/路名)
            short_loc = location.split("区")[0] + "区" if "区" in location else location[:20]

            # 兼容字符串默认值
            cur_air_str = f"{cur_air:.1f}" if isinstance(cur_air, (int, float)) else str(cur_air)
            peak_max_str = f"{peak_max:.1f}" if isinstance(peak_max, (int, float)) else str(peak_max)
            peak_max_air_str = f"{peak_max_air:.1f}" if isinstance(peak_max_air, (int, float)) else str(peak_max_air)
            cur_t_str = f"{cur_t:.1f}" if isinstance(cur_t, (int, float)) else str(cur_t)

            col_content = (
                f"**{emoji} {p['name']}**\n"
                f"\n"
                f"📍 {short_loc}\n"
                f"\n"
                f"**当前** ({cur_time_short}):\n"
                f"路表 **{cur_t_str}°C** · 气温 {cur_air_str}°C\n"
                f"\n"
                f"**14 天峰值** ({peak_max_date} @ {peak_max_time}):\n"
                f"路表 **{peak_max_str}°C** · 气温 {peak_max_air_str}°C\n"
                f"等级: {a['label']}"
            )
            columns.append({
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": col_content}}
                ]
            })
        elements.append({
            "tag": "column_set",
            "flex_mode": "stretch",
            "columns": columns
        })

    # 4. 最高等级建议
    if top_level in ("orange", "red"):
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"## {top_emoji} 最高等级: **{sorted_pts[0]['alert_summary']['label']}**\n"
                           f"\n"
                           f"> 💡 {sorted_pts[0]['alert_summary']['advice']}"
            }
        })

    # 5. footer
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": f"🕐 数据拉取: {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
            {"tag": "plain_text", "content": "🧮 模型: SHRP/LTPP 经验公式 v0.1 | 📡 数据源: Open-Meteo + 高德地图"}
        ]
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {
                    "tag": "plain_text",
                    "content": f"🌡️ PaveTherm Sentinel · 批量监测 ({len(points_alerts)} 点)"
                },
            },
            "elements": elements
        },
    }


# ============================================================
# V2: 重排版 - 解决"信息挤一团"问题
# ============================================================
def render_multi_point_card_v2(points_alerts: list, window_days: int | None = None) -> dict:
    """
    批量卡片 V2: 清新版排版
    - 全展开 (A 选项)
    - 每个点单独 div 块 + 内嵌 hr 分隔 (飞书不支持每个点都 collapsible_panel)
    - 字段拆行: 现在/峰值分独立 block,不再一行堆
    window_days: 查询窗口天数 (None=14, 数字=实际查询的天数),卡片文案动态
    """
    if not points_alerts:
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"template": "grey", "title": {"tag": "plain_text", "content": "🌡️ PaveTherm Sentinel"}},
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "⚠️ 暂无监测点"}}],
            }
        }

    level_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_pts = sorted(
        points_alerts,
        key=lambda x: (level_order.get(x["alert_summary"]["level"], 9),
                       -(x.get("peak_14d_max") or 0))  # 组内也按 14 天峰值降序
    )
    top_level = sorted_pts[0]["alert_summary"]["level"]
    template = LEVEL_TO_TEMPLATE.get(top_level, "grey")

    # ====== 整页统计 ======
    counts = {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    for x in points_alerts:
        counts[x["alert_summary"]["level"]] += 1

    # 14 天全网最危险
    global_peak = max(points_alerts, key=lambda x: x.get("peak_14d_max") or 0)
    gp_temp = global_peak.get("peak_14d_max") or 0
    gp_level = classify_level(gp_temp)["level"]
    gp_emoji = LEVEL_TO_EMOJI[gp_level]

    elements = []

    # ====== 区块 1: 整页摘要 ======
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📊 整页摘要** · 共 **{len(points_alerts)}** 个监测点"
        }
    })

    # 4 列大数 (留白更多)
    elements.append({
        "tag": "column_set",
        "flex_mode": "stretch",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"🔴 红警\n\n# **{counts['red']}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"🟠 橙警\n\n# **{counts['orange']}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"🟡 黄警\n\n# **{counts['yellow']}**"}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"🟢 绿警\n\n# **{counts['green']}**"}}
            ]},
        ]
    })

    # ====== 区块 2: 全网最危险日 ======
    # ====== 动态窗口文案 (根据 window_days 或实际数据) ======
    if window_days is None or window_days >= 14:
        window_noun = "14 天"
        range_noun = "14 天内"
    else:
        window_noun = f"未来 {window_days} 天"
        range_noun = f"{window_days} 天内"

    if global_peak.get("peak_14d_max_date"):
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**⚠️ {window_noun}全网最危险日**\n"
                    f"📅 **{global_peak['peak_14d_max_date']}** @ "
                    f"**{global_peak.get('peak_14d_max_time', '??:??')}**\n"
                    f"{gp_emoji} **{global_peak['point']['name']}** → "
                    f"**{gp_temp:.1f}°C** ({classify_level(gp_temp)['label']})"
                )
            }
        })

    # ====== 区块 3: 监测点详情 ======
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📋 监测点详情** · 按 *预警等级 + {range_noun}峰值* 降序"
        }
    })

    for i, x in enumerate(sorted_pts, 1):
        p = x["point"]
        a = x["alert_summary"]
        cur_t = x.get("current_temp", 0)
        cur_air = x.get("current_air_temp")
        cur_time_short = (x.get("current_time") or "")[11:16] if x.get("current_time") else "??:??"
        peak_max = x.get("peak_14d_max", 0) or 0
        peak_max_date = x.get("peak_14d_max_date", "?")
        peak_max_time = x.get("peak_14d_max_time", "??:??")
        peak_max_air = x.get("peak_14d_max_air")
        location = p.get("geocode_display") or p.get("location_input", "")
        short_loc = location.split("区")[0] + "区" if "区" in location else location[:20]

        cur_air_s = f"{cur_air:.1f}" if isinstance(cur_air, (int, float)) else str(cur_air)
        peak_max_s = f"{peak_max:.1f}"
        peak_air_s = f"{peak_max_air:.1f}" if isinstance(peak_max_air, (int, float)) else "?"

        # 等级 emoji (按 14 天峰值,不是当前)
        peak_level = classify_level(peak_max)["level"]
        peak_emoji = LEVEL_TO_EMOJI[peak_level]

        # 标题: 序号 + 当前等级 (按现在温度判) + 14 天峰值等级 (按峰值判)
        cur_emoji = LEVEL_TO_EMOJI[a["level"]]

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"### {i}. {p['name']}\n"
                    f"📍 {short_loc}\n"
                    f"\n"
                    f"**当前状态** (北京时间 {cur_time_short}):  {cur_emoji}\n"
                    f"├ 路表温度: **{cur_t:.1f}°C**\n"
                    f"├ 气温: {cur_air_s}°C\n"
                    f"└ 当前等级: **{a['label']}**\n"
                    f"\n"
                    f"**{range_noun}峰值**:  {peak_emoji}\n"
                    f"├ 时间: **{peak_max_date} @ {peak_max_time}**\n"
                    f"├ 路表温度: **{peak_max_s}°C**\n"
                    f"├ 当时气温: {peak_air_s}°C\n"
                    f"└ 峰值等级: **{classify_level(peak_max)['label']}**"
                )
            }
        })
        # 点与点之间加分隔线 (最后一点不加)
        if i < len(sorted_pts):
            elements.append({"tag": "hr"})

    # ====== 区块 4: footer ======
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": f"🕐 数据拉取: {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
            {"tag": "plain_text", "content": "🧮 模型: SHRP/LTPP 经验公式 v0.1 | 📡 数据源: Open-Meteo + 高德"},
            {"tag": "plain_text", "content": f"📐 排版: V2 清新版 · 查询窗口: {window_noun}"},
        ]
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {
                    "tag": "plain_text",
                    "content": f"🌡️ PaveTherm Sentinel · 批量监测 ({len(points_alerts)} 点) V2"
                },
            },
            "elements": elements
        },
    }


# ====== CLI ======

if __name__ == "__main__":
    print("=== alert.py 阈值测试 ===")
    for t in [25, 50, 54.9, 55, 58, 60, 63, 65, 70]:
        a = classify_level(t)
        print(f"  {t:5.1f}°C → {a['label']:25} ({a['level']}) — {a['advice']}")
