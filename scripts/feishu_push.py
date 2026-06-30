#!/usr/bin/env python3
"""
feishu_push.py - 飞书消息推送 (markdown 模式,基于 lark-cli)

为什么用 markdown 而不是 interactive 卡片:
  - lark-cli 1.0.x 的 --markdown 参数自动包装为 post 类型,100% 兼容
  - interactive 卡片 JSON 200621 报错 (parse card json err),可能 v1 模板兼容性问题
  - post 类型的 markdown 已支持: 表格/加粗/引用/链接/emoji 颜色,够用
  - 后续如需真卡片再切 v2 card,先跑通闭环

支持:
  - 推送单点 markdown (from monitor.query_point)
  - 推送批量 markdown (from monitor.query_batch)
  - 纯文本 / raw markdown
"""
import sys
import os
import json
import subprocess
import argparse
from pathlib import Path

# 自动加载 ~/.hermes/secrets/pavetherm-sentinel.env (无需用户手动 source)
# 这是 .env 风格: KEY=VALUE 行 + # 注释
def _load_secrets_env():
    env_path = Path.home() / ".hermes" / "secrets" / "pavetherm-sentinel.env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            # 只设置没在 os.environ 里有的 (避免覆盖用户当前 shell 的值)
            os.environ.setdefault(k, v)
    except Exception:
        pass

_load_secrets_env()

# lark-cli 路径: 优先 env, fallback 用 PATH 里的 lark-cli
LARK_CLI = os.environ.get("PAVETHERM_LARK_CLI", "lark-cli")

# home chat_id: 优先从 ~/.hermes/secrets env 读 (共享),否则 fallback 到 config.yaml
# 注意: fallback 必须是空串而非硬编码 chat_id, 避免泄漏用户的 chat_id 到公开仓库
try:
    from config_loader import load_config as _load_config
    _cfg = _load_config(strict=False)
    FEISHU_HOME_CHAT_ID = (
        os.environ.get("PAVETHERM_FEISHU_CHAT_ID")
        or _cfg.get("alert_delivery", {}).get("feishu_home_chat_id")
        or ""
    )
except Exception:
    FEISHU_HOME_CHAT_ID = os.environ.get("PAVETHERM_FEISHU_CHAT_ID", "")


def _run_lark(args: list, timeout: int = 30) -> dict:
    """调 lark-cli 通用封装"""
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip(), "stdout": result.stdout}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "非 JSON 返回", "stdout": result.stdout}


def push_markdown(chat_id: str, markdown: str, identity: str = "bot") -> dict:
    """
    推送 markdown (自动包装为 post 类型)。
    这是当前最稳的方式,避开 200621 卡片 parse 错误。
    """
    return _run_lark([
        LARK_CLI, "im", "+messages-send",
        "--as", identity,
        "--chat-id", chat_id,
        "--markdown", markdown,
    ])


def push_text(chat_id: str, text: str, identity: str = "bot") -> dict:
    """纯文本"""
    return _run_lark([
        LARK_CLI, "im", "+messages-send",
        "--as", identity,
        "--chat-id", chat_id,
        "--text", text,
    ])


def push_card(chat_id: str, card: dict, identity: str = "bot") -> dict:
    """
    推飞书 interactive 卡片 (msg_type=interactive)。

    飞书 API 真实要求: content 字段是字符串化的卡片 JSON,
    即 {"config":..., "header":..., "elements":[...]}, 不需要再包 "card" 字段。
    lark-cli --content 接受 dict 或 JSON 字符串, 它会再 stringify 一次。
    """
    # 兼容两种 card 入参:
    # 1) 完整 {"msg_type":"interactive","card":{...}} (alert.py render_feishu_card 输出)
    # 2) 纯 card dict {"header":...,"elements":...}
    if "card" in card:
        card_inner = card["card"]
    else:
        card_inner = card

    # 关键: content 字符串就是 card dict 的 JSON, 不加 "card" 包装
    return _run_lark([
        LARK_CLI, "im", "+messages-send",
        "--as", identity,
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", json.dumps(card_inner, ensure_ascii=False),
    ])


# ====== Markdown 渲染器 (把 monitor result 转成 markdown) ======

def temp_to_dual(celsius: float) -> str:
    f = celsius * 9 / 5 + 32
    return f"{celsius:.1f}°C / {f:.1f}°F"


LEVEL_ICONS = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}

# 兼容旧名:monitor.py 旧版用 `from feishu_push import send as feishu_send`
send = push_markdown


def r0_window_days(results: list) -> int | None:
    """从 results[0]['slice_window'] 抽 window_days, 给 V2 卡片文案动态化用
    无窗口信息 (None) → 返回 None, 卡片按 14 天口径渲染
    """
    if not results:
        return None
    win = results[0].get("slice_window") or {}
    days = win.get("days")
    if days is not None:
        return int(days)
    count = win.get("count")
    if count is not None and 0 < count <= 14:
        return int(count)
    return None
LEVEL_NAMES = {
    "green": "绿-正常",
    "yellow": "黄-注意",
    "orange": "橙-警戒",
    "red": "红-危险",
}


def render_point_markdown(result: dict) -> str:
    """单点 → markdown"""
    p = result["point"]
    cur = result["current"]
    peaks = result["daily_peaks"]
    a = result["alert_summary"]

    lines = [
        f"## 🌡️ {p['name']} - 路表温度监测",
        "",
        f"**位置**: {p.get('geocode_display') or p.get('location_input')}  ",
        f"**坐标**: ({p.get('lat', '?')}, {p.get('lon', '?')})  ",
        f"**路面**: {p.get('pavement_color', 'unknown')} · {p.get('pavement_type') or '?'} · 老化 {p.get('pavement_age_years') or '未知'}年",
        "",
        "---",
        "",
        "### 📊 当前状态",
        "",
        f"- **路表温度**: **{cur['pavement_temp']:.1f}°C**",
        f"- 气温: {cur['air_temp']:.1f}°C",
        f"- 太阳辐射: {cur.get('gti_w_m2', 0):.0f} W/m²",
        f"- 模型置信度: `{cur.get('confidence', '?')}`",
        "",
        "---",
        "",
        f"### ⚠️ 预警: {LEVEL_ICONS[a['level']]} {a['label']}",
        "",
        f"> {a['advice']}",
        "",
        "---",
        "",
        "### 📅 未来 14 天路表温度峰值",
        "",
        "| 日期 | 等级 | 路表峰值 | 当天气温 | Δ差值 | 时间 |",
        "|------|------|----------|----------|-------|------|",
    ]
    for d in peaks[:14]:
        lvl = classify_level_inline(d["peak_pavement_temp"])
        icon = LEVEL_ICONS[lvl]
        c = d["peak_pavement_temp"]
        a = d.get("peak_air_temp", 0)
        delta = c - a
        lines.append(
            f"| {d['date']} | {icon} | **{c:.1f}°C** | {a:.1f}°C | +{delta:.1f}°C | {d['peak_time'][11:16]} |"
        )

    # 高风险日
    high = [d for d in peaks if classify_level_inline(d["peak_pavement_temp"]) in ("orange", "red")]
    if high:
        lines.append("")
        lines.append("### 🔥 高风险日 (橙/红预警)")
        lines.append("")
        for d in high:
            lines.append(f"- **{d['date']}**: {d['peak_pavement_temp']:.1f}°C @ {d['peak_time'][11:16]}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_🕐 数据拉取: {p.get('last_query', '-')[:19] if p.get('last_query') else '本次实时'}_  ")
    lines.append(f"_🧮 模型: SHRP/LTPP 经验公式 v0.1 (参数偏保守,宁误报不漏报)_  ")
    lines.append(f"_📡 数据源: Open-Meteo + 高德地图_")
    return "\n".join(lines)


def render_batch_markdown(results: list) -> str:
    """批量 → markdown"""
    if not results:
        return "⚠️ 暂无监测点数据"

    level_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_pts = sorted(results, key=lambda x: (level_order.get(x["alert_summary"]["level"], 9), -x["current_temp"]))

    lines = [
        f"## 📡 PaveTherm Sentinel · 批量监测报告",
        "",
        f"**共 {len(results)} 个监测点** (按预警等级 + 路表温度降序)",
        "",
        "| # | 等级 | 监测点 | 路表温度 | 状态 |",
        "|---|------|--------|----------|------|",
    ]
    for i, x in enumerate(sorted_pts[:20], 1):
        p = x["point"]
        a = x["alert_summary"]
        lines.append(
            f"| {i} | {LEVEL_ICONS[a['level']]} | {p['name']} | **{x['current_temp']:.1f}°C** | {LEVEL_NAMES[a['level']]} |"
        )

    top = sorted_pts[0]
    if top["alert_summary"]["level"] in ("orange", "red"):
        lines.append("")
        lines.append(f"### ⚠️ 最高等级")
        lines.append("")
        lines.append(f"> {top['alert_summary']['advice']}")
    lines.append("")
    lines.append(f"_📡 数据源: Open-Meteo + 高德地图 · 🕐 {sorted_pts[0]['point'].get('last_query', '')[:19]}_")
    return "\n".join(lines)


def classify_level_inline(temp: float) -> str:
    """内联阈值分类 (默认配置)"""
    if temp >= 65:
        return "red"
    if temp >= 60:
        return "orange"
    if temp >= 55:
        return "yellow"
    return "green"


# ====== 入口函数 ======

def push_point(chat_id: str, point_id: str, identity: str = "bot", use_card: bool = True) -> dict:
    """
    单点: query_point + render + push
    use_card=True (默认): 推飞书真卡片
    use_card=False: 推 markdown
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from monitor import query_point
    result = query_point(point_id)
    if "error" in result:
        return {"ok": False, "error": result["error"]}
    if use_card:
        return push_card(chat_id, result["feishu_card"], identity=identity)
    else:
        md = render_point_markdown(result)
        return push_markdown(chat_id, md, identity=identity)


def push_batch(chat_id: str, results: list, identity: str = "bot",
               use_card: bool = True, version: str = "v2") -> dict:
    """
    批量: render + push
    use_card=True (默认): 推飞书真卡片
    version="v1": 旧版 (column_set 2 列网格)
    version="v2": 清新版 (默认,分块+全展开+分线)
    """
    if use_card:
        from alert import render_multi_point_card as render_v1
        from alert import render_multi_point_card_v2 as render_v2
        render_fn = render_v2 if version == "v2" else render_v1
        # 转成 alert.py render_multi_point_card 期望的格式
        pts_alerts = [{
            "point": r["point"],
            "current_temp": r["current_temp"],
            "current_air_temp": r.get("current_air_temp"),
            "current_time": r.get("current_time"),
            "alert_summary": r["alert_summary"],
            "peak_14d_max": r.get("peak_14d_max", r["current_temp"]),
            "peak_14d_max_date": r.get("peak_14d_max_date"),
            "peak_14d_max_time": r.get("peak_14d_max_time"),
            "peak_14d_max_air": r.get("peak_14d_max_air"),
        } for r in results]

        card = render_fn(pts_alerts, window_days=r0_window_days(results))
        return push_card(chat_id, card, identity=identity)
    else:
        md = render_batch_markdown(results)
        return push_markdown(chat_id, md, identity=identity)


# ====== CLI ======

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="飞书推送 (markdown 模式)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # text
    p_t = sub.add_parser("text", help="发纯文本")
    p_t.add_argument("--chat-id", required=True)
    p_t.add_argument("--content", required=True)
    p_t.add_argument("--identity", dest="identity", default="bot", choices=["bot", "user"])

    # markdown (raw)
    p_m = sub.add_parser("markdown", help="发 raw markdown")
    p_m.add_argument("--chat-id", required=True)
    p_m.add_argument("--content", required=True)
    p_m.add_argument("--identity", dest="identity", default="bot", choices=["bot", "user"])

    # point (单点)
    p_p = sub.add_parser("point", help="单点监测 → 飞书卡片 (默认) / markdown")
    p_p.add_argument("--chat-id", required=True)
    p_p.add_argument("--point-id", required=True)
    p_p.add_argument("--identity", dest="identity", default="bot", choices=["bot", "user"])
    p_p.add_argument("--markdown", action="store_true", help="改推 markdown 而非真卡片")

    # batch (批量)
    p_b = sub.add_parser("batch", help="批量监测 → 飞书卡片 (默认) / markdown")
    p_b.add_argument("--chat-id", required=True)
    p_b.add_argument("--all", action="store_true", help="全选所有点")
    p_b.add_argument("--filter", help="过滤如 city=成都")
    p_b.add_argument("--identity", dest="identity", default="bot", choices=["bot", "user"])
    p_b.add_argument("--markdown", action="store_true", help="改推 markdown 而非真卡片")

    args = parser.parse_args()

    if args.cmd == "text":
        r = push_text(args.chat_id, args.content, identity=args.identity)
    elif args.cmd == "markdown":
        r = push_markdown(args.chat_id, args.content, identity=args.identity)
    elif args.cmd == "point":
        r = push_point(args.chat_id, args.point_id, identity=args.identity, use_card=not args.markdown)
    elif args.cmd == "batch":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from monitor import query_batch
        kwargs = {}
        if args.filter and "=" in args.filter:
            k, v = args.filter.split("=", 1)
            kwargs[f"filter_{k}"] = v
        if not args.all and not kwargs:
            print("❌ --all 或 --filter 至少给一个", file=sys.stderr)
            sys.exit(1)
        results = query_batch(**kwargs)
        r = push_batch(args.chat_id, results, identity=args.identity, use_card=not args.markdown)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(r, ensure_ascii=False, indent=2))
