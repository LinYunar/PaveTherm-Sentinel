#!/usr/bin/env python3
"""
geocode.py - 坐标解析模块

支持两种输入:
  1. 精准坐标: "30.6586, 104.0648" 或 "30.6586 104.0648"
  2. 模糊地址: "成都三环路成渝立交" → 高德地图 (国内 POI 匹配最佳)

输出: dict with lat, lon, source, confidence, display_name
"""
import os
import re
import sys
import json
import time
import yaml
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# ===== 路径配置 =====
SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.yaml"

# 精准坐标正则: 4-7 位小数 (高德精度到小数后 6 位)
COORD_PATTERN = re.compile(
    r'^\s*(-?\d{1,3}\.\d{3,7})[,\s]+(-?\d{1,3}\.\d{3,7})\s*$'
)

# 委托给统一配置加载器 (支持 env 注入 key)
try:
    from config_loader import load_config as _load_config
except ImportError:
    _load_config = None


def load_config() -> dict:
    if _load_config is not None:
        return _load_config(strict=False)
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"config.yaml 不存在: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ===== 精准坐标识别 =====
def parse_coordinate_input(text: str) -> dict | None:
    if not text:
        return None
    m = COORD_PATTERN.match(text.strip())
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "source": "manual",
        "confidence": 1.0,
        "display_name": f"精准坐标 {lat:.6f}, {lon:.6f}",
    }


# ===== 高德地图地理编码 =====
def geocode_amap(query: str, api_key: str) -> dict | None:
    """
    高德 Web 服务 - 地理编码 API
    文档: https://lbs.amap.com/api/webservice/guide/api/georegeo
    """
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {
        "key": api_key,
        "address": query,
        "city": "",  # 不限城市,全国搜
        "output": "json",
    }
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers={"User-Agent": "PaveTherm-Sentinel/0.1"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[geocode] 高德 HTTP 错误: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[geocode] 高德 JSON 解析失败: {e}", file=sys.stderr)
        return None

    # 高德返回格式: { status, info, geocodes: [...] }
    if data.get("status") != "1":
        print(f"[geocode] 高德返回错误: status={data.get('status')} info={data.get('info')}", file=sys.stderr)
        return None

    geocodes = data.get("geocodes", [])
    if not geocodes:
        return None

    top = geocodes[0]
    # 高德 location 格式: "lng,lat"
    location = top.get("location", "")
    if "," not in location:
        return None
    lon_str, lat_str = location.split(",", 1)

    # 高德返回的 level 字段 (POI 类别) 可作为置信度参考
    level = top.get("level", "")
    confidence = _amap_confidence(level, top)

    return {
        "lat": float(lat_str),
        "lon": float(lon_str),
        "source": "amap",
        "confidence": confidence,
        "display_name": top.get("formatted_address", query),
        "level": level,
        "adcode": top.get("adcode"),
        "raw": top,
    }


def _amap_confidence(level: str, raw: dict) -> float:
    """
    基于高德 level 字段粗估置信度。
    参考: https://lbs.amap.com/api/webservice/guide/tools/infolevel
    """
    # 道路/路口 - 高置信
    if level in ("道路名", "交叉路口", "桥", "高速"):
        return 0.95
    # POI - 中高
    if level in ("地名地址信息", "兴趣点", "门牌号", "公交站"):
        return 0.85
    # 区/市 - 低,作为兜底
    if level in ("省/直辖市/特别行政区/自治区", "市/区/县", "商圈"):
        return 0.6
    return 0.7


# ===== Nominatim 兜底 (海外或特殊场景) =====
def geocode_nominatim(query: str) -> dict | None:
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers={"User-Agent": "PaveTherm-Sentinel/0.1"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[geocode] Nominatim 错误: {e}", file=sys.stderr)
        return None
    if not data:
        return None

    top = data[0]
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "source": "nominatim",
        "confidence": 0.75,
        "display_name": top.get("display_name", query),
        "level": top.get("class", ""),
        "raw": top,
    }


# ===== 主入口 =====
def geocode(text: str, config: dict | None = None) -> dict | None:
    """
    主入口: 自动判断坐标/地址。
    优先级: 精准坐标 → 高德 → Nominatim → None
    """
    if config is None:
        config = load_config()

    # 1) 精准坐标直通
    coord = parse_coordinate_input(text)
    if coord:
        return coord

    geo_cfg = config.get("geocoding", {})
    provider = geo_cfg.get("provider", "amap")

    # 2) 高德 (主)
    if provider == "amap" and geo_cfg.get("amap_key"):
        result = geocode_amap(text, geo_cfg["amap_key"])
        if result:
            return result

    # 3) Nominatim 兜底
    result = geocode_nominatim(text)
    if result:
        return result

    # 4) 都没查到
    return None


# ===== CLI =====
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 geocode.py '30.6586, 104.0648'    # 精准坐标")
        print("  python3 geocode.py '成都三环路成渝立交'    # 模糊地址")
        sys.exit(1)

    query = sys.argv[1]
    print(f"[geocode] 查询: {query}")
    start = time.time()
    result = geocode(query)
    elapsed = time.time() - start

    if result is None:
        print(f"[geocode] ❌ 未找到 ({elapsed:.2f}s)")
        print("  兜底建议: 直接给精准坐标,格式 '纬度,经度'")
        sys.exit(2)

    print(f"[geocode] ✅ 命中 ({elapsed:.2f}s)")
    print(f"  lat/lon  : {result['lat']:.6f}, {result['lon']:.6f}")
    print(f"  source   : {result['source']}")
    print(f"  conf     : {result['confidence']:.2f}")
    print(f"  display  : {result['display_name']}")
    print(f"  level    : {result.get('level', '-')}")
    if result.get('adcode'):
        print(f"  adcode   : {result['adcode']}")
