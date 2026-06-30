#!/usr/bin/env python3
"""
render.py - 把 monitor.py 输出渲染为 markdown 文本 (用于飞书推送)

把 interactive card 降级为 markdown, 飞书会自动渲染为富文本 post。
"""
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from alert import temp_to_dual, classify_level


def render_single_point_markdown(result: dict) -> str:
    """单点: result 是 monitor.query_point() 返回值"""
    p = result["point"]
    cur = result["current"]
    peaks = result["daily_peaks"]
    alert = result["alert_summary"]

    lines = []
    lines.append(f"🌡️ **{p['name']}**")
    lines.append(f"📍 {p.get('geocode_display') or p.get('location_input')} ({p.get('lat'):.4f}, {p.get('lon'):.4f})")
    lines.append(f"🛣️ 路面: {p.get('pavement_color', 'unknown')} · {p.get('pavement_type') or '-'} · 老化 {p.get('pavement_age_years') or '未知'}年")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"📊 **当前路表温度**")
    lines.append(f"• 路表: **{temp_to_dual(cur['pavement_temp'])}**")
    lines.append(f"• 气温: {temp_to_dual(cur['air_temp'])}")
    lines.append(f"• 太阳辐射: {cur.get('gti_w_m2', 0):.0f} W/m²")
    lines.append(f"• 模型置信度: {cur.get('confidence', '?')}")
    lines.append("")
    lines.append(f"⚠️ **{alert['label']}** — {alert['advice']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("📅 **未来 14 天路表温度峰值** (11:00-16:00 窗口)")
    icons = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
    for d in peaks[:14]:
        lvl = classify_level(d["peak_pavement_temp"])["level"]
        lines.append(f"{d['date']}  {icons[lvl]}  {temp_to_dual(d['peak_pavement_temp']):>16}  (峰值 {d['peak_time'][11:16]})")

    high_risk = [d for d in peaks if classify_level(d["peak_pavement_temp"])["level"] in ("orange", "red")]
    if high_risk:
        lines.append("")
        lines.append("🔥 **高风险日 (橙/红预警):**")
        for d in high_risk:
            lvl = classify_level(d["peak_pavement_temp"])["level"]
            lines.append(f"  • {d['date']}  {icons[lvl]}  {temp_to_dual(d['peak_pavement_temp'])}")

    lines.append("")
    lines.append("---")
    lines.append(f"_🕐 {p.get('last_query', '-')[:19]} · 模型: SHRP/LTPP v0.1 (偏保守) · PaveTherm Sentinel_")
    return "\n".join(lines)


def render_batch_markdown(results: list) -> str:
    """批量: results 是 monitor.query_batch() 返回值列表"""
    lines = []
    lines.append(f"🌡️ **PaveTherm Sentinel · 批量监测报告**")
    lines.append(f"共 **{len(results)}** 个点位")
    lines.append("")
    level_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_pts = sorted(results, key=lambda x: (level_order.get(x["alert_summary"]["level"], 9), -x["current_temp"]))
    icons = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
    for i, r in enumerate(sorted_pts, 1):
        p = r["point"]
        a = r["alert_summary"]
        c = r["current_temp"]
        lines.append(f"{i:2d}. {icons[a['level']]} **{p['name']}** — {temp_to_dual(c)} ({a['label']})")
    if sorted_pts:
        top = sorted_pts[0]
        if top["alert_summary"]["level"] in ("orange", "red"):
            lines.append("")
            lines.append(f"⚠️ **最高等级**: {top['alert_summary']['label']}")
            lines.append(top["alert_summary"]["advice"])
    lines.append("")
    lines.append("_排序: 预警等级 → 路表温度降序 · PaveTherm Sentinel_")
    return "\n".join(lines)


# ===== CLI =====
if __name__ == "__main__":
    # 单独跑这个文件没意义, 给 monitor.py 调用
    print("render.py 是 helper, 请通过 monitor.py 调用")
