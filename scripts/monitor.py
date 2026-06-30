#!/usr/bin/env python3
"""
monitor.py - PaveTherm Sentinel 主入口

全链路: 监测点 → 天气拉取 → 路表温度建模 → 预警 → 飞书卡片 / CSV / Dashboard

使用:
  # 单点查询
  python3 monitor.py query <point_id>                    # 飞书卡片
  python3 monitor.py query <point_id> --export csv       # 导出 CSV
  python3 monitor.py query <point_id> --export dashboard # 重新生成 dashboard

  # 批量查询
  python3 monitor.py batch --filter city=成都
  python3 monitor.py batch --all

  # 增删监测点
  python3 monitor.py add <name_or_coord> [--pavement-color black] [--age 5]
  python3 monitor.py remove <point_id>
  python3 monitor.py list
"""
import sys
import json
import csv
import yaml
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from geocode import geocode
from fetch_weather import fetch_open_meteo
from model import compute_pavement_temp, compute_hourly_pavement, daily_peak
from alert import classify_level, render_feishu_card, render_multi_point_card, temp_to_dual

# 历史遗留模块 (可能是旧版本写的) - 容错导入,失败不影响核心流程
try:
    from render import render_single_point_markdown, render_batch_markdown
except (ImportError, ModuleNotFoundError, AttributeError) as e:
    render_single_point_markdown = None
    render_batch_markdown = None
try:
    from feishu_push import send as feishu_send
except (ImportError, ModuleNotFoundError, AttributeError) as e:
    feishu_send = None

DATA_PATH = SKILL_ROOT / "data" / "monitoring_points.yaml"
EXPORT_DIR = SKILL_ROOT / "exports"
EXPORT_DIR.mkdir(exist_ok=True)


# ===== YAML 读写 =====
def load_points() -> dict:
    if not DATA_PATH.exists():
        return {"monitoring_points": [], "defaults": {}, "scheduled_tasks": []}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_points(data: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ===== 单点完整查询 =====
def query_point(point_id: str, days: int | None = None, from_date: str | None = None) -> dict:
    """
    端到端: 给定点位 id,返回飞书卡片 + 完整数据
    days: 截取未来 N 天的峰值 (None 或 1-14),None=全部 14 天
    from_date: 起始日期 YYYY-MM-DD,None=今天
    """
    # ===== 截取窗口计算 (实际过滤在 peaks 拿到后做) =====
    if days is not None and (days < 1 or days > 14):
        return {"error": f"--days 必须在 1-14 范围,现在是 {days}"}

    db = load_points()
    point = next((p for p in db["monitoring_points"] if p["id"] == point_id), None)
    if not point:
        return {"error": f"未找到监测点: {point_id}"}

    lat, lon = point["lat"], point["lon"]
    if lat is None or lon is None:
        # 尝试 geocode
        geo = geocode(point["location_input"])
        if geo:
            lat, lon = geo["lat"], geo["lon"]
            point["lat"] = lat
            point["lon"] = lon
            point["geocoded"] = True
            point["geocode_source"] = geo["source"]
            point["geocode_confidence"] = geo["confidence"]
            point["geocode_display"] = geo["display_name"]
            save_points(db)
        else:
            return {"error": f"坐标缺失且 geocode 失败: {point['location_input']}"}

    # 1) 天气
    weather = fetch_open_meteo(lat, lon)
    if not weather:
        return {"error": "天气拉取失败"}

    # 2) 建模 - 当前
    cur = weather["current"]
    current_pav = compute_pavement_temp(
        air_temp=cur["temperature_2m"],
        # 当前无 GTI, 用 daily 晴阴状况估算 (保守取 500 W/m², 实际下午)
        gti=500 if cur.get("is_day") else 0,
        wind_speed=cur.get("wind_speed_10m"),
        color=point.get("pavement_color", "unknown"),
        age_years=point.get("pavement_age_years"),
    )

    # 3) 建模 - 14 天逐小时
    hourly_pav = compute_hourly_pavement(weather, point)
    peaks = daily_peak(hourly_pav)

    # 4) 预警 - 用今日峰值
    today_peak = peaks[0] if peaks else None
    current_for_alert = current_pav
    if today_peak and today_peak["peak_pavement_temp"] > current_pav["pavement_temp"]:
        # 当前已过高温窗口,用今日预测峰值更准确
        current_for_alert = {
            **current_pav,
            "pavement_temp": today_peak["peak_pavement_temp"],
            "air_temp": today_peak["peak_air_temp"],
        }

    alert_summary = classify_level(current_for_alert["pavement_temp"], point.get("warning_thresholds"))

    # 5) 更新点元数据
    point["last_query"] = datetime.now().isoformat(timespec="seconds")
    point["last_alert_level"] = alert_summary["level"]
    save_points(db)

    # ===== 6) 截取窗口 (days / from_date) =====
    peaks_for_card = peaks
    if from_date:
        # 找 from_date 开始的索引
        try:
            start_idx = next(i for i, pk in enumerate(peaks) if pk["date"] >= from_date)
        except StopIteration:
            start_idx = 0
    else:
        start_idx = 0
    if days is not None:
        peaks_for_card = peaks[start_idx:start_idx + days]
    elif from_date:
        peaks_for_card = peaks[start_idx:]
    else:
        peaks_for_card = peaks  # 全 14 天

    # 7) 渲染飞书卡片 (用截取后的 peaks)
    card = render_feishu_card(point, current_for_alert, peaks_for_card, alert_summary)

    # 8) 窗口内峰值 (跟 query_batch 字段对齐,便于复用 _assert_match 自检)
    if peaks_for_card:
        win_max = max(peaks_for_card, key=lambda pk: pk["peak_pavement_temp"])
        win_max_temp = win_max["peak_pavement_temp"]
        win_max_date = win_max["date"]
        win_max_time = win_max["peak_time"][11:16]
        win_max_air = win_max["peak_air_temp"]
    else:
        win_max_temp = win_max_date = win_max_time = win_max_air = None

    return {
        "point": point,
        "current": current_for_alert,
        "current_temp": current_for_alert["pavement_temp"],  # 兼容 query_batch 字段
        "current_air_temp": cur["temperature_2m"],
        "current_time": cur.get("time"),
        "daily_peaks": peaks,  # 全 14 天 (返回数据用)
        "daily_peaks_sliced": peaks_for_card,  # 截取后 (卡片用)
        "slice_window": {"days": days, "from_date": from_date, "count": len(peaks_for_card)},
        "alert_summary": alert_summary,
        "peak_14d_max": win_max_temp,
        "peak_14d_max_date": win_max_date,
        "peak_14d_max_time": win_max_time,
        "peak_14d_max_air": win_max_air,
        "feishu_card": card,
        "raw_weather": weather,  # 留底
    }


def export_csv(result: dict, out_path: Path | None = None) -> Path:
    """单点 14 天峰值导出 CSV"""
    point = result["point"]
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = EXPORT_DIR / f"{point['id']}_{ts}.csv"

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "peak_pavement_temp_c", "peak_pavement_temp_f", "peak_time", "peak_air_temp_c", "alert_level"])
        for p in result["daily_peaks"]:
            level = classify_level(p["peak_pavement_temp"])["level"]
            writer.writerow([
                p["date"],
                p["peak_pavement_temp"],
                round(p["peak_pavement_temp"] * 9 / 5 + 32, 1),
                p["peak_time"],
                p["peak_air_temp"],
                level,
            ])
    return out_path


def query_batch(filter_city: str | None = None, point_ids: list | None = None,
                days: int | None = None, from_date: str | None = None) -> list:
    """批量查询 (并行加速: N 个点从 N×~1.3s 降到 ~1.3s)

    days / from_date: 限定窗口,影响 peak_14d_max_xxx (不再用全 14 天,而是窗口内)
    """
    if days is not None and (days < 1 or days > 14):
        raise ValueError(f"--days 必须在 1-14 范围,现在是 {days}")
    db = load_points()
    pts = db["monitoring_points"]
    if filter_city:
        pts = [p for p in pts if filter_city in (p.get("geocode_display") or p.get("location_input") or "")]
    if point_ids:
        pts = [p for p in pts if p["id"] in point_ids]

    # 1) 处理未 geocode 的点 (用 ThreadPoolExecutor 并行)
    from concurrent.futures import ThreadPoolExecutor
    def _geocode_point(p):
        if not p.get("geocoded"):
            loc = p.get("location_query") or p["location_input"]
            geo = geocode(loc)
            if geo:
                p["lat"], p["lon"] = geo["lat"], geo["lon"]
                p["geocoded"] = True
                p["geocode_source"] = geo["source"]
                p["geocode_confidence"] = geo["confidence"]
                p["geocode_display"] = geo["display_name"]
        return p

    with ThreadPoolExecutor(max_workers=min(8, len(pts) or 1)) as ex:
        pts = list(ex.map(_geocode_point, pts))

    # 过滤无坐标点
    pts = [p for p in pts if p.get("lat") is not None]

    # 2) 并行拉 Open-Meteo (每点 1 次请求, 8 线程并发)
    def _fetch_weather(p):
        return p, fetch_open_meteo(p["lat"], p["lon"])

    weather_map = {}
    with ThreadPoolExecutor(max_workers=min(8, len(pts) or 1)) as ex:
        for p, w in ex.map(_fetch_weather, pts):
            if w:
                weather_map[p["id"]] = w

    # 3) 串行算 model + 渲染 (CPU bound, 不并行意义不大)
    results = []
    for p in pts:
        weather = weather_map.get(p["id"])
        if not weather:
            continue
        cur = weather["current"]
        cp = compute_pavement_temp(
            air_temp=cur["temperature_2m"],
            gti=500 if cur.get("is_day") else 0,
            wind_speed=cur.get("wind_speed_10m"),
            color=p.get("pavement_color", "unknown"),
            age_years=p.get("pavement_age_years"),
        )
        hp = compute_hourly_pavement(weather, p)
        peaks = daily_peak(hp)
        if peaks:
            today_peak = peaks[0]
            if today_peak["peak_pavement_temp"] > cp["pavement_temp"]:
                cp["pavement_temp"] = today_peak["peak_pavement_temp"]
        alert_s = classify_level(cp["pavement_temp"], p.get("warning_thresholds"))
        p["last_query"] = datetime.now().isoformat(timespec="seconds")
        p["last_alert_level"] = alert_s["level"]

        # ====== 窗口截取 ======
        if from_date:
            try:
                start_idx = next(i for i, pk in enumerate(peaks) if pk["date"] >= from_date)
            except StopIteration:
                start_idx = 0
        else:
            start_idx = 0
        if days is not None:
            sliced_peaks = peaks[start_idx:start_idx + days]
        elif from_date:
            sliced_peaks = peaks[start_idx:]
        else:
            sliced_peaks = peaks

        # 在 sliced 窗口内找峰值 (V2 卡片用的字段)
        window_max = max(sliced_peaks, key=lambda pk: pk["peak_pavement_temp"]) if sliced_peaks else None
        results.append({
            "point": p,
            "current_temp": cp["pavement_temp"],
            "current_air_temp": cur["temperature_2m"],
            "current_time": cur.get("time"),
            "alert_summary": alert_s,
            "peak_14d_max": window_max["peak_pavement_temp"] if window_max else cp["pavement_temp"],
            "peak_14d_max_date": window_max["date"] if window_max else None,
            "peak_14d_max_time": window_max["peak_time"][11:16] if window_max else None,
            "peak_14d_max_air": window_max["peak_air_temp"] if window_max else None,
            "daily_peaks": peaks,  # 全 14 天数据保留
            "daily_peaks_sliced": sliced_peaks,  # 截取后
            "slice_window": {"days": days, "from_date": from_date, "count": len(sliced_peaks)},
        })
    save_points(db)
    return results


# ===== CLI =====
def main():
    parser = argparse.ArgumentParser(description="PaveTherm Sentinel - 沥青路表温度监测")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # query
    p_q = sub.add_parser("query", help="查询单个监测点")
    p_q.add_argument("point_id", help="监测点 ID (如 cd_chengyulukou_001)")
    p_q.add_argument("--export", choices=["feishu", "csv", "dashboard", "console"], default="feishu")
    p_q.add_argument("--send-feishu", action="store_true", help="同时把结果推送到飞书当前对话")
    p_q.add_argument("--days", type=int, default=14, help="未来天数 1-14 (默认14)")
    p_q.add_argument("--from-date", help="起始日期 YYYY-MM-DD (默认今天)")

    # batch
    p_b = sub.add_parser("batch", help="批量查询")
    p_b.add_argument("--filter", help="过滤条件, 如 city=成都")
    p_b.add_argument("--all", action="store_true", help="全部监测点")
    p_b.add_argument("--export", choices=["feishu", "csv", "console", "send-feishu"], default="feishu")
    p_b.add_argument("--days", type=int, default=14, help="未来天数 1-14 (默认14,影响卡片信息密度)")
    p_b.add_argument("--from-date", help="起始日期 YYYY-MM-DD (默认今天)")
    p_b.add_argument("--include-14d-table", action="store_true", help="批量卡片里附加每点 14 天表 (字符上限风险)")

    # add
    p_a = sub.add_parser("add", help="添加监测点")
    p_a.add_argument("location", help="道路名 或 'lat,lon'")
    p_a.add_argument("--city", help="城市/区县 hint, 防歧义 (例: '康定', '成都')")
    p_a.add_argument("--pavement-color", choices=["black", "gray", "light_gray", "unknown"], default="unknown")
    p_a.add_argument("--age", type=float, help="路面使用年数")
    p_a.add_argument("--type", default="road_intersection")
    p_a.add_argument("--pavement-type", help="AC-13/SMA-13/AC-16 等")
    p_a.add_argument("--yellow", type=float, default=55)
    p_a.add_argument("--orange", type=float, default=60)
    p_a.add_argument("--red", type=float, default=65)
    p_a.add_argument("--yes", "-y", action="store_true", help="跳过坐标合理性确认 (高级用户)")

    # list
    sub.add_parser("list", help="列出所有监测点")

    # remove
    p_r = sub.add_parser("remove", help="删除监测点")
    p_r.add_argument("point_id")

    args = parser.parse_args()

    # ===== query =====
    if args.cmd == "query":
        result = query_point(args.point_id, days=args.days, from_date=args.from_date)
        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(2)

        if args.export == "feishu":
            # 输出 JSON 给上游 (飞书消息推送) 用
            print(json.dumps(result["feishu_card"], ensure_ascii=False, indent=2))
            print(f"\n# 摘要: {result['point']['name']} 当前路表 {result['current']['pavement_temp']}°C → {result['alert_summary']['label']}", file=sys.stderr)
        elif args.export == "csv":
            path = export_csv(result)
            print(f"✅ CSV 已导出: {path}")
        elif args.export == "console":
            # 终端友好输出 (优先用 sliced 窗口)
            display_peaks = result.get("daily_peaks_sliced") or result["daily_peaks"]
            window = result.get("slice_window", {})
            window_label = f" ({window.get('count', len(display_peaks))}/{len(result['daily_peaks'])} 天)"
            p = result["point"]
            print(f"\n=== {p['name']} ===")
            print(f"位置: {p.get('geocode_display')} ({p.get('lat')}, {p.get('lon')})")
            print(f"当前路表: {result['current']['pavement_temp']}°C / 气温 {result['current']['air_temp']}°C")
            print(f"预警: {result['alert_summary']['label']}")
            if window.get("days") or window.get("from_date"):
                print(f"窗口: days={window.get('days')}, from_date={window.get('from_date')}")
            print(f"\n峰值:{window_label}")
            for d in display_peaks:
                lvl = classify_level(d["peak_pavement_temp"])["level"]
                icons = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
                print(f"  {d['date']}  {icons[lvl]}  {d['peak_pavement_temp']:5.1f}°C  @ {d['peak_time'][11:16]}")
        elif args.export == "dashboard":
            print("Dashboard 生成 (TODO: 接入 dashboard.py)")

        # 飞书推送 (无论 export 是什么, --send-feishu 都会触发)
        if args.send_feishu or args.export == "send-feishu":
            from feishu_push import push_markdown, FEISHU_HOME_CHAT_ID
            md = render_single_point_markdown(result)
            push_result = push_markdown(FEISHU_HOME_CHAT_ID, md)
            if push_result.get("ok"):
                print(f"\n✅ 已推送到飞书: message_id={push_result.get('data', {}).get('message_id')}", file=sys.stderr)
            else:
                print(f"\n❌ 飞书推送失败: {push_result.get('error')}", file=sys.stderr)

    # ===== batch =====
    elif args.cmd == "batch":
        kwargs = {}
        if args.filter:
            if "=" in args.filter:
                k, v = args.filter.split("=", 1)
                kwargs[f"filter_{k}"] = v
        if args.days is not None:
            kwargs["days"] = args.days
        if args.from_date:
            kwargs["from_date"] = args.from_date
        try:
            results = query_batch(**kwargs)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(2)
        if not results:
            print("⚠️  无匹配监测点")
            return
        if args.export == "feishu":
            # 走 feishu_push.push_batch -> 转换格式 + V2 渲染
            from feishu_push import push_batch, FEISHU_HOME_CHAT_ID
            res = push_batch(FEISHU_HOME_CHAT_ID, results, use_card=True, version="v2")
            print(json.dumps(res, ensure_ascii=False, indent=2))
            window = results[0].get("slice_window", {}) if results else {}
            print(f"\n# 窗口: {window}", file=sys.stderr)
        elif args.export == "console":
            window = results[0].get("slice_window", {}) if results else {}
            if window.get("days") or window.get("from_date"):
                print(f"# 窗口: {window}")
            level_icons = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
            for r in sorted(results, key=lambda x: -x["current_temp"]):
                p = r["point"]
                a = r["alert_summary"]
                peak_d = r.get("peak_14d_max_date", "?")
                peak_t = r.get("peak_14d_max_time", "??:??")
                print(f"  {level_icons[a['level']]} {p['name']:30} 路表 {r['current_temp']:5.1f}°C  峰值 {r.get('peak_14d_max',0):5.1f}°C @ {peak_d} {peak_t}  {a['label']}")
        elif args.export == "csv":
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = EXPORT_DIR / f"batch_{ts}.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id", "name", "lat", "lon", "current_pavement_temp", "peak_14d_max", "peak_date", "peak_time", "alert_level"])
                for r in results:
                    p = r["point"]
                    w.writerow([p["id"], p["name"], p.get("lat"), p.get("lon"),
                                r["current_temp"], r["peak_14d_max"],
                                r.get("peak_14d_max_date", ""), r.get("peak_14d_max_time", ""),
                                r["alert_summary"]["level"]])
            print(f"✅ CSV 已导出: {path}")
        elif args.export == "send-feishu":
            # 兼容旧命令 (调 feishu_push 推送 markdown 格式)
            md = render_batch_markdown(results)
            from feishu_push import push_markdown, FEISHU_HOME_CHAT_ID
            push_result = push_markdown(FEISHU_HOME_CHAT_ID, md)
            if push_result.get("ok"):
                print(f"✅ 已推送到飞书: message_id={push_result.get('data', {}).get('message_id')}")
            else:
                print(f"❌ 飞书推送失败: {push_result.get('error')}")

    # ===== add =====
    elif args.cmd == "add":
        # Bug #1: --city hint 拼到 location 前面防高德歧义
        # 例: "东大街" + --city 康定 → "康定市东大街" (避免被解析成"上海康定东路")
        query_location = args.location
        if args.city:
            # 智能拼接: city 已含 "市/县/区" 直接拼; 否则补 "市" 防"康定东大街"被解析成"上海康定东路"
            # 例: --city 康定 → "康定市东大街" (✓); --city 成都 → "成都市东大街" (✓)
            # 非中文 city (如 "Chengdu") 直接拼空格, 不加"市"
            if any('一' <= c <= '鿿' for c in args.city):
                city_with_suffix = args.city if args.city[-1] in "市区县" else f"{args.city}市"
                query_location = f"{city_with_suffix}{args.location}"
            else:
                query_location = f"{args.city} {args.location}"
            print(f"💡 加入 --city hint, 查询用: {query_location}")

        geo = geocode(query_location)
        if not geo:
            print(f"❌ 地理编码失败: {query_location}", file=sys.stderr)
            print(f"   试试: monitor.py add {args.location!r} --city <城市名>", file=sys.stderr)
            sys.exit(2)

        # Bug #3: bbox 验证 (中国陆地范围: lat 18-54, lon 73-135)
        # 高德歧义时可能返回境外/同名异地点
        lat_ok = 18 <= geo["lat"] <= 54
        lon_ok = 73 <= geo["lon"] <= 135
        if not (lat_ok and lon_ok):
            print(f"⚠️ 坐标异常: ({geo['lat']}, {geo['lon']}) - 不在中国陆地范围", file=sys.stderr)
            print(f"   高德返回: {geo.get('display_name')}", file=sys.stderr)
            print(f"   这通常是高德地名歧义 (同名地点在外)", file=sys.stderr)
            print(f"   建议: monitor.py add {args.location!r} --city <正确城市名>", file=sys.stderr)
            sys.exit(3)

        # Bug #3 进阶: --city hint 与返回结果的 city 比对
        if args.city and not args.yes:
            display = geo.get("display_name", "")
            city_hint = args.city.replace("市", "").replace("县", "").replace("区", "")
            if city_hint not in display:
                print(f"⚠️ 城市 hint 不匹配:")
                print(f"   你指定: --city {args.city}")
                print(f"   高德返回: {display}")
                print(f"   这可能不是你想要的点!")
                print(f"   加 --yes 跳过确认 / 改 --city 重试")
                sys.exit(4)

        db = load_points()
        from hashlib import md5
        # 用 query_location (含 city hint) 生成 id, 同名不同城市 id 不同
        pid = "pt_" + md5(query_location.encode()).hexdigest()[:8]
        new_point = {
            "id": pid,
            "name": args.location,
            "location_type": args.type,
            "location_input": args.location,
            "location_query": query_location,  # 实际高德查询的字符串
            "lat": geo["lat"],
            "lon": geo["lon"],
            "geocoded": True,
            "geocode_source": geo["source"],
            "geocode_confidence": geo["confidence"],
            "geocode_display": geo["display_name"],
            "pavement_color": args.pavement_color,
            "pavement_age_years": args.age,
            "pavement_age_source": "user" if args.age else None,
            "pavement_age_confidence": "CONFIRMED" if args.age else "SPECULATIVE",
            "pavement_type": args.pavement_type,
            "warning_thresholds": {"yellow": args.yellow, "orange": args.orange, "red": args.red},
            "created_at": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }
        db["monitoring_points"].append(new_point)
        save_points(db)
        print(f"✅ 已添加: {pid} - {new_point['name']}")
        print(f"   坐标: {geo['lat']}, {geo['lon']} (置信度 {geo['confidence']})")
        print(f"   显示: {geo['display_name']}")
        if not args.age:
            print(f"   ⚠️ 路面年龄未填,模型按 {db.get('defaults', {}).get('pavement_age_fallback_years', 7)} 年兜底 (置信度 🔴 SPECULATIVE)")
        if args.pavement_color == "unknown":
            print(f"   💡 建议: 路面颜色会显著影响模型精度")
            print(f"      - 新铺沥青 (黑色): black")
            print(f"      - 中度老化 (灰黑): gray")
            print(f"      - 严重老化 (灰白): light_gray")

    # ===== list =====
    elif args.cmd == "list":
        db = load_points()
        pts = db["monitoring_points"]
        if not pts:
            print("⚠️  暂无监测点, 用 `add` 添加")
            return
        print(f"共 {len(pts)} 个监测点:\n")
        for p in pts:
            print(f"  [{p['id']}] {p['name']}")
            print(f"    位置: {p.get('geocode_display') or p['location_input']}  ({p.get('lat')}, {p.get('lon')})")
            print(f"    路面: {p.get('pavement_color', 'unknown')} · 老化 {p.get('pavement_age_years') or '未知'}年")
            print(f"    阈值: 黄 {p.get('warning_thresholds', {}).get('yellow', 55)} / 橙 {p.get('warning_thresholds', {}).get('orange', 60)} / 红 {p.get('warning_thresholds', {}).get('red', 65)} °C")
            if p.get("last_alert_level"):
                level_icons = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
                print(f"    上次: {p.get('last_query', '-')[:19]}  等级 {level_icons.get(p['last_alert_level'], '?')} {p['last_alert_level']}")
            print()

    # ===== remove =====
    elif args.cmd == "remove":
        db = load_points()
        before = len(db["monitoring_points"])
        db["monitoring_points"] = [p for p in db["monitoring_points"] if p["id"] != args.point_id]
        after = len(db["monitoring_points"])
        if before == after:
            print(f"❌ 未找到: {args.point_id}")
            sys.exit(1)
        save_points(db)
        print(f"✅ 已删除: {args.point_id}")


if __name__ == "__main__":
    main()
