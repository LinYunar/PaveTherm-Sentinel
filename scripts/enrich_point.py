#!/usr/bin/env python3
"""
enrich_point.py - 监测点元数据自动补全

当 pavement_color / pavement_age_years 缺失时,从多源自动推断:
  1. OSM Overpass API: 查区域 landuse/highway 类型 (推测路面类型)
  2. 高德 POI: 查道路/桥梁 POI 类型与建成年份 (如有)
  3. Tavily search: 搜 [地名 + "建成年份/修筑时间/大修"]
  4. JTG F40 / GB 50092: 中国沥青路面常见铺装时间推断

输出: 更新 monitoring_points.yaml 中点位的:
  - pavement_color
  - pavement_age_years
  - pavement_age_source (osm | amap | tavily | default)
  - pavement_age_confidence (CONFIRMED | ASSUMED | SPECULATIVE)
  - pavement_type
"""
import sys
import json
import re
import yaml
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

DATA_PATH = SKILL_ROOT / "data" / "monitoring_points.yaml"
CONFIG_PATH = SKILL_ROOT / "config.yaml"


# 委托给统一配置加载器 (支持从 ~/.hermes/secrets 读 key)
try:
    from config_loader import load_config as _load_config
except ImportError:
    # 独立运行兜底 (旧代码也能跑)
    _load_config = None


def load_config() -> dict:
    if _load_config is not None:
        return _load_config(strict=False)
    # 兜底: 直接读 yaml (无 env 注入,key 会是 ${...} 字面量,需用户自己 resolve)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ====== 1. OSM Overpass 查询 ======
def query_overpass(lat: float, lon: float, radius_m: int = 200) -> dict:
    """
    Overpass API: 查 (lat, lon) 周边 radius_m 范围内的道路/区域信息。
    返回 {highway_type, landuse, surface, name}
    """
    # 查道路类型 + 路面材质
    query = f"""
    [out:json][timeout:10];
    (
      way(around:{radius_m},{lat},{lon})["highway"];
      way(around:{radius_m},{lat},{lon})["landuse"];
      way(around:{radius_m},{lat},{lon})["surface"];
    );
    out tags;
    """
    try:
        url = "https://overpass-api.de/api/interpreter"
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "PaveTherm-Sentinel/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [overpass] 错误: {e}")
        return {}

    elements = result.get("elements", [])
    if not elements:
        return {}

    # 聚合所有 tag
    tags_agg = {}
    for el in elements:
        for k, v in el.get("tags", {}).items():
            tags_agg.setdefault(k, set()).add(v)

    # 简化: 取最常见的值
    simplified = {k: list(v) if len(v) > 1 else list(v)[0] for k, v in tags_agg.items()}
    return simplified


# ====== 2. 高德 POI 查询 ======
def query_amap_poi(keyword: str, api_key: str) -> dict:
    """
    高德 POI 搜索: 查 keyword 的详细 POI 信息 (含 type, address)。
    不直接返回建成年份, 但能确认这是桥/路/立交。
    """
    url = "https://restapi.amap.com/v3/place/text"
    params = {
        "key": api_key,
        "keywords": keyword,
        "output": "json",
        "extensions": "base",
    }
    try:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full_url, headers={"User-Agent": "PaveTherm-Sentinel/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [amap] 错误: {e}")
        return {}

    pois = data.get("pois", [])
    if not pois:
        return {}
    top = pois[0]
    return {
        "name": top.get("name"),
        "type": top.get("type"),
        "address": top.get("address"),
        "location": top.get("location"),
        "pcode": top.get("pcode"),
        "adcode": top.get("adcode"),
    }


# ====== 3. Tavily 搜索 (查建成年份) ======
def query_tavily(query: str, api_key: str) -> list:
    """
    Tavily AI 搜索: 查道路/立交的建成/大修时间。
    返回搜索结果列表 [{title, url, content}, ...]
    """
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": 5,
        "search_depth": "basic",
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "PaveTherm-Sentinel/0.1"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [tavily] 错误: {e}")
        return []
    return result.get("results", [])


def parse_age_from_text(text: str, current_year: int = 2026) -> float | None:
    """
    从文本中提取建成/大修年份, 计算路面使用年数。
    模式: 2020 年建成 / 2015 年大修 / 2018 年改扩建 / 2003 年通车
    """
    patterns = [
        r"(\d{4})\s*年[通车建成使用开放]",  # 2020 年通车
        r"(\d{4})\s*年[大修翻新重建改建扩建]",  # 2015 年大修
        r"(\d{4})\s*年.{0,5}沥青",  # 2018 年铺筑沥青
        r"建于\s*(\d{4})",  # 建于 2003
        r"(\d{4})\s*年\s*\d{1,2}\s*月",  # 2010 年 5 月
    ]
    candidates = []
    for p in patterns:
        for m in re.finditer(p, text):
            year = int(m.group(1))
            if 1980 <= year <= current_year:
                candidates.append(year)

    if not candidates:
        return None
    # 取最近的一年 (最新的大修/建成,作为"路面年龄"的基线)
    latest = max(candidates)
    return current_year - latest


# ====== 4. JTG F40 / GB 50092 推断 (中国沥青路面常见规格) ======
def infer_pavement_type(road_grade: str, age_years: float | None) -> dict:
    """
    基于 JTG F40-2004 / GB 50092-96 推断路面类型和颜色。
    高速/城市快速路: SMA-13 / AC-13, 新铺黑色
    国省干线: AC-16 / AC-20, 中度老化 5-8 年后变灰
    市政道路: AC-13, 频繁翻修
    """
    if "expressway" in road_grade.lower() or "highway" in road_grade.lower():
        return {
            "pavement_type": "SMA-13 / AC-13",
            "typical_color": "black",
            "note": "高速/快速路常用改性沥青 SMA,新铺黑色,5年后变深灰"
        }
    elif "municipal" in road_grade.lower() or "urban" in road_grade.lower():
        return {
            "pavement_type": "AC-13",
            "typical_color": "gray" if (age_years and age_years > 5) else "black",
            "note": "市政道路常用 AC-13,5年内黑色,5-10年灰色,10+年灰白"
        }
    return {
        "pavement_type": "AC-16",
        "typical_color": "gray",
        "note": "默认 AC-16 (通用沥青混合料)"
    }


# ====== 主函数 ======
def enrich_point(point: dict) -> dict:
    """
    给一个监测点补全元数据。
    返回 dict: {color, age_years, age_source, age_confidence, pavement_type}
    """
    cfg = load_config()
    amap_key = cfg.get("geocoding", {}).get("amap_key", "")
    tavily_key = cfg.get("search", {}).get("tavily_key", "")

    lat, lon = point.get("lat"), point.get("lon")
    name = point.get("name", "")
    location_input = point.get("location_input", name)
    road_grade = point.get("road_grade", "urban_expressway")

    result = {
        "color": None,
        "age_years": None,
        "age_source": None,
        "age_confidence": None,
        "pavement_type": None,
    }

    if lat is None or lon is None:
        print("  ⚠️  无坐标, 跳过 OSM 查询")
    else:
        print(f"  → OSM 查询 ({lat}, {lon}) 周边 200m...")
        osm = query_overpass(lat, lon)
        if osm:
            surface = osm.get("surface", [])
            if isinstance(surface, list) and surface:
                surface = surface[0]
            print(f"     surface = {surface}, highway = {osm.get('highway', [])}")
            if surface and "asphalt" in str(surface).lower():
                result["pavement_type"] = "AC 沥青 (OSM 验证)"

    if amap_key:
        print(f"  → 高德 POI 查询: {name}...")
        poi = query_amap_poi(name, amap_key)
        if poi:
            print(f"     type = {poi.get('type')}, address = {poi.get('address')}")
            # 立交/桥/道路 POI 类型
            if any(k in str(poi.get("type", "")) for k in ["桥", "立交", "道路", "高速", "桥粱"]):
                result["pavement_type"] = result.get("pavement_type") or "AC 沥青 (高德 POI 确认道路)"

    if tavily_key:
        print(f"  → Tavily 搜索: {name} 大修/翻修时间...")
        # 优先搜"大修/翻修/铣刨重铺", 这种年份才是"路面年龄"
        results = query_tavily(f"{name} 沥青路面 大修 OR 翻修 OR 铣刨重铺 OR 中修", tavily_key)
        for r in results[:3]:
            content = r.get("content", "")
            title = r.get("title", "")
            text = f"{title} {content}"
            age = parse_age_from_text(text)
            if age is not None:
                result["age_years"] = age
                result["age_source"] = "tavily (大修)"
                result["age_confidence"] = "ASSUMED"
                print(f"     找到: {title[:50]} → 推断 {age} 年 (大修时间)")
                break
        if result["age_years"] is None:
            print(f"     未找到大修时间, 兜底搜建成年份...")
            results = query_tavily(f"{name} 建成年份 OR 通车时间", tavily_key)
            for r in results[:3]:
                text = f"{r.get('title', '')} {r.get('content', '')}"
                age = parse_age_from_text(text)
                if age is not None:
                    # 道路建成, 但默认 8 年前大修过 1 次
                    road_age = age
                    last_overhaul = max(0, road_age - 8)
                    result["age_years"] = last_overhaul
                    result["age_source"] = f"tavily (建成→推算大修: 建成{road_age}年-8年大修周期)"
                    result["age_confidence"] = "SPECULATIVE"
                    print(f"     找到: {r.get('title', '')[:50]} → 建成{road_age}年, 推算大修后 {last_overhaul} 年")
                    break
    else:
        print(f"  ⚠️  Tavily key 未配置, 跳过建成年份搜索")

    # 推断路面类型
    if not result["pavement_type"]:
        inferred = infer_pavement_type(road_grade, result["age_years"])
        result["pavement_type"] = inferred["pavement_type"]

    # 推断颜色
    age = result["age_years"]
    if age is None:
        result["color"] = "unknown"
        result["color_source"] = "default"
    elif age <= 2:
        result["color"] = "black"
        result["color_source"] = "JTG F40 推断"
    elif age <= 7:
        result["color"] = "gray"
        result["color_source"] = "JTG F40 推断"
    else:
        result["color"] = "light_gray"
        result["color_source"] = "JTG F40 推断"

    return result


# ====== CLI ======
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="监测点元数据自动补全")
    parser.add_argument("point_id", nargs="?", help="监测点 ID (不填则全部)")
    parser.add_argument("--apply", action="store_true", help="应用到 YAML (默认只 dry-run)")
    parser.add_argument("--force", action="store_true", help="强制重新 enrich (即使已有元数据)")
    args = parser.parse_args()

    if not DATA_PATH.exists():
        print(f"❌ {DATA_PATH} 不存在")
        sys.exit(1)

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        db = yaml.safe_load(f)

    targets = db["monitoring_points"]
    if args.point_id:
        targets = [p for p in targets if p["id"] == args.point_id]
        if not targets:
            print(f"❌ 未找到: {args.point_id}")
            sys.exit(1)

    for p in targets:
        print(f"\n=== {p['id']}: {p['name']} ===")
        # 强制 enrich 模式 (除非 --skip-if-complete 显式指定)
        if not args.force and p.get("pavement_color") and p["pavement_color"] != "unknown" and p.get("pavement_age_years"):
            print(f"  ✅ 已有完整元数据 (color={p['pavement_color']}, age={p['pavement_age_years']}), 跳过 (用 --force 强制重查)")
            continue
        result = enrich_point(p)
        print(f"\n  📋 推断结果:")
        for k, v in result.items():
            print(f"    {k}: {v}")
        if args.apply:
            if result.get("color"):
                p["pavement_color"] = result["color"]
            if result.get("age_years") is not None:
                p["pavement_age_years"] = result["age_years"]
                p["pavement_age_source"] = result.get("age_source", "default")
                p["pavement_age_confidence"] = result.get("age_confidence", "SPECULATIVE")
            if result.get("pavement_type"):
                p["pavement_type"] = result["pavement_type"]
            p["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            print(f"  ✅ 已写入")
        else:
            print(f"  💡 加 --apply 才会写入 YAML")

    if args.apply:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(db, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        print(f"\n✅ 已保存 {DATA_PATH}")
