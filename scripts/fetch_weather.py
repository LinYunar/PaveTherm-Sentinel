#!/usr/bin/env python3
"""
fetch_weather.py - Open-Meteo 天气数据拉取

输入: lat, lon
输出: 标准化数据结构
  {
    "current": {time, temperature_2m, weather_code, is_day, wind_speed_10m},
    "hourly": [{time, temperature_2m, global_tilted_irradiance, cloud_cover, wind_speed_10m}, ...],
    "daily": [{date, temp_max, temp_min, sunrise, sunset, precipitation}, ...],
    "meta": {elevation, timezone, generation_time_ms}
  }
"""
import sys
import json
import yaml
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.yaml"

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# 委托给统一配置加载器
try:
    from config_loader import load_config as _load_config
except ImportError:
    _load_config = None


def load_config() -> dict:
    if _load_config is not None:
        return _load_config(strict=False)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_open_meteo(lat: float, lon: float, config: dict | None = None) -> dict | None:
    """
    拉取 Open-Meteo 当前 + 14 天预报。
    关键字段: temperature_2m, global_tilted_irradiance (GTI, 瓦片辐射), cloud_cover
    """
    if config is None:
        config = load_config()
    weather_cfg = config.get("weather", {})

    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
        "forecast_days": weather_cfg.get("forecast_days", 14),
        "timezone": "Asia/Shanghai",
        "hourly": ",".join([
            "temperature_2m",
            "global_tilted_irradiance",  # 瓦片辐射 W/m² (路面真实吸热)
            "cloud_cover",
            "wind_speed_10m",
        ]),
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "sunrise",
            "sunset",
            "precipitation_sum",
        ]),
    }
    url = f"{WEATHER_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "PaveTherm-Sentinel/0.1"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[weather] 拉取失败: {e}", file=sys.stderr)
        return None

    return normalize(raw)


def normalize(raw: dict) -> dict:
    """把 Open-Meteo 原始结构转成更易用的形式"""
    # === current ===
    cw = raw.get("current_weather", {})
    current = {
        "time": cw.get("time"),
        "temperature_2m": cw.get("temperature"),
        "wind_speed_10m": cw.get("windspeed"),
        "wind_direction": cw.get("winddirection"),
        "weather_code": cw.get("weathercode"),
        "is_day": cw.get("is_day"),
    }

    # === hourly (按时间合并) ===
    h = raw.get("hourly", {})
    h_times = h.get("time", [])
    h_temps = h.get("temperature_2m", [])
    h_gti = h.get("global_tilted_irradiance", [])
    h_cloud = h.get("cloud_cover", [])
    h_wind = h.get("wind_speed_10m", [])

    hourly = []
    for i, t in enumerate(h_times):
        hourly.append({
            "time": t,
            "temperature_2m": h_temps[i] if i < len(h_temps) else None,
            "global_tilted_irradiance": h_gti[i] if i < len(h_gti) else None,
            "cloud_cover": h_cloud[i] if i < len(h_cloud) else None,
            "wind_speed_10m": h_wind[i] if i < len(h_wind) else None,
        })

    # === daily ===
    d = raw.get("daily", {})
    d_times = d.get("time", [])
    d_max = d.get("temperature_2m_max", [])
    d_min = d.get("temperature_2m_min", [])
    d_sunrise = d.get("sunrise", [])
    d_sunset = d.get("sunset", [])
    d_precip = d.get("precipitation_sum", [])

    daily = []
    for i, day in enumerate(d_times):
        daily.append({
            "date": day,
            "temp_max": d_max[i] if i < len(d_max) else None,
            "temp_min": d_min[i] if i < len(d_min) else None,
            "sunrise": d_sunrise[i] if i < len(d_sunrise) else None,
            "sunset": d_sunset[i] if i < len(d_sunset) else None,
            "precipitation": d_precip[i] if i < len(d_precip) else None,
        })

    return {
        "current": current,
        "hourly": hourly,
        "daily": daily,
        "meta": {
            "elevation": raw.get("elevation"),
            "timezone": raw.get("timezone"),
            "generation_time_ms": raw.get("generationtime_ms"),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


# ===== CLI =====
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 fetch_weather.py <lat> <lon>")
        print("  例: python3 fetch_weather.py 30.6344 104.1516")
        sys.exit(1)

    lat = float(sys.argv[1])
    lon = float(sys.argv[2])

    print(f"[weather] 拉取 lat={lat}, lon={lon}")
    data = fetch_open_meteo(lat, lon)
    if data is None:
        print("[weather] ❌ 拉取失败")
        sys.exit(2)

    # 摘要输出
    cur = data["current"]
    print(f"\n=== 当前 ===")
    print(f"  时间     : {cur['time']}")
    print(f"  气温     : {cur['temperature_2m']} °C")
    print(f"  风速     : {cur['wind_speed_10m']} m/s")
    print(f"  天气码   : {cur['weather_code']} ({'白天' if cur['is_day'] else '夜晚'})")

    print(f"\n=== 14 天预报 ===")
    for d in data["daily"][:14]:
        flag = "🌧" if (d.get("precipitation") or 0) > 0 else "  "
        print(f"  {d['date']}  {flag}  {d['temp_min']:>5.1f} ~ {d['temp_max']:>5.1f} °C")

    print(f"\n=== 元数据 ===")
    print(f"  海拔     : {data['meta']['elevation']} m")
    print(f"  时区     : {data['meta']['timezone']}")
    print(f"  拉取时间 : {data['meta']['fetched_at']}")

    # 详细 hourly 只输出今天 8:00~20:00 (高温窗口)
    print(f"\n=== 今日高温窗口 (11:00-16:00) hourly ===")
    today = data["hourly"][0]["time"][:10] if data["hourly"] else ""
    for h in data["hourly"]:
        if h["time"].startswith(today):
            hour = int(h["time"][11:13])
            if 8 <= hour <= 20:
                gti = h.get("global_tilted_irradiance")
                print(f"  {h['time']}  T={h['temperature_2m']:>5.1f}°C  GTI={gti:>6.1f}W/m²  云={h['cloud_cover']:>3}%")
