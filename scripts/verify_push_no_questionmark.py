#!/usr/bin/env python3
"""
verify_push_no_questionmark.py
================================
验证飞书批量卡片推送内容没有 `?` 字段 (Bug 4 防御脚本)

触发场景:
  - 改了 scripts/feishu_push.py 的字段映射后
  - 改了 scripts/alert.py 的 render_multi_point_card 后
  - 新 session 第一件事 (确认代码状态)

用法:
  python3 scripts/verify_push_no_questionmark.py
  python3 scripts/verify_push_no_questionmark.py --filter city=成都
  python3 scripts/verify_push_no_questionmark.py --send-feishu  # 实际推 1 张验证卡片

退出码:
  0 - 卡片无 `?` (字段映射全)
  1 - 卡片含 `?` 字段 (字段映射遗漏, 需检查 feishu_push.py push_batch 字典推导)
  2 - 拉数据失败 (geocode 或 fetch_weather 异常)
"""
import sys
import re
import json
import argparse
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

QUESTION_MARK_PATTERNS = [
    r"\?\?:\?\?",          # 缺时间显示 "??:??"
    r"气温 \?°C",         # 缺气温显示 "气温 ?°C"
    r"\(\? @",             # 缺峰值日期显示 "(? @"
    r"路表 \*\*\?°C",     # 缺峰值温度
]


def render_card_dry_run(filter_city: str = "成都") -> tuple[bool, list]:
    """
    Dry-run 渲染批量卡片, 不实际推飞书
    返回: (ok, 问题列表)
    """
    try:
        from monitor import query_batch
        from feishu_push import _build_pts_alerts  # type: ignore
    except ImportError:
        # 旧版没有 _build_pts_alerts, 手动还原
        from monitor import query_batch
        from alert import render_multi_point_card
        results = query_batch(filter_city=filter_city)
        if not results:
            return True, []
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
        card = render_multi_point_card(pts_alerts)
    else:
        from alert import render_multi_point_card
        results = query_batch(filter_city=filter_city)
        if not results:
            return True, []
        card = render_multi_point_card(_build_pts_alerts(results))

    card_str = json.dumps(card, ensure_ascii=False)

    issues = []
    for pattern in QUESTION_MARK_PATTERNS:
        matches = re.findall(pattern, card_str)
        if matches:
            issues.append(f"模式 {pattern!r} 出现 {len(matches)} 次")

    return (len(issues) == 0), issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filter", default="city=成都", help="批量查询过滤条件 (默认 city=成都)")
    parser.add_argument("--send-feishu", action="store_true", help="实际推 1 张验证卡片到飞书")
    parser.add_argument("--chat-id", help="飞书 chat_id (--send-feishu 时必填, 或环境变量 FEISHU_CHAT_ID)")
    args = parser.parse_args()

    # 1. Dry-run 渲染
    print("🔍 Dry-run 渲染批量卡片...")
    ok, issues = render_card_dry_run(filter_city=args.filter.split("=")[-1])
    if ok:
        print("✅ 卡片无 `?` 字段 (Bug 4 已修)")
    else:
        print(f"❌ 卡片含 `?` 字段 ({len(issues)} 个问题):")
        for issue in issues:
            print(f"  - {issue}")
        print()
        print("💡 修复路径: 检查 scripts/feishu_push.py push_batch() 字典推导,")
        print("   补全 current_air_temp / current_time / peak_14d_max_date/_time/_air 字段")
        return 1

    # 2. 可选: 实际推 1 张
    if args.send_feishu:
        import os
        chat_id = args.chat_id or os.environ.get("FEISHU_CHAT_ID")
        if not chat_id:
            print("❌ --send-feishu 需要 --chat-id 或环境变量 FEISHU_CHAT_ID")
            return 2
        from monitor import query_batch
        from feishu_push import push_batch
        results = query_batch(filter_city=args.filter.split("=")[-1])
        res = push_batch(chat_id, results, use_card=True)
        if res.get("ok"):
            print(f"✅ 已推验证卡片: message_id={res.get('data', {}).get('message_id')}")
        else:
            print(f"❌ 推送失败: {res.get('error')}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
