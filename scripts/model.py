#!/usr/bin/env python3
"""
model.py - 沥青路表温度建模

核心公式 (综合 SHRP/LTPP 经验 + 颜色老化修正):
  T_pavement = T_air
             + α_rad × GTI                  # 太阳辐射升温
             + α_color × I_color × GTI_norm # 路面颜色吸收率
             + Δ_age(age_years)             # 老化年数补偿
             - α_wind × wind_speed          # 风冷修正

所有 α 系数基于 LTPP / SHRP 经验值,后续用实测数据校准。
当前参数偏保守 (风险预警优先,宁误报不漏报)。
"""
import yaml
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.yaml"

# 委托给统一配置加载器
try:
    from config_loader import load_config as _load_config
except ImportError:
    _load_config = None


def load_model_config() -> dict:
    if _load_config is not None:
        return _load_config(strict=False).get("pavement_model", {})
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f).get("pavement_model", {})


def get_age_correction(age_years: float | None, age_table: dict) -> float:
    """
    老化年数补偿 (°C):
      - 0 年新铺: 0
      - 5-10 年: 升温 +0.5~+1.0 (表面氧化,颜色变深)
      - 20+ 年: 下降 (表面剥落,颜色变浅)
    """
    if age_years is None:
        return age_table.get(5, 0.5)  # 未知按 5 年中度老化
    # 线性插值
    years_sorted = sorted([int(k) for k in age_table.keys()])
    if age_years <= years_sorted[0]:
        return age_table[years_sorted[0]]
    if age_years >= years_sorted[-1]:
        return age_table[years_sorted[-1]]
    for i in range(len(years_sorted) - 1):
        y0, y1 = years_sorted[i], years_sorted[i + 1]
        if y0 <= age_years <= y1:
            v0, v1 = age_table[y0], age_table[y1]
            return v0 + (v1 - v0) * (age_years - y0) / (y1 - y0)
    return 0.0


def compute_pavement_temp(
    air_temp: float,
    gti: float | None,
    wind_speed: float | None,
    color: str = "unknown",
    age_years: float | None = None,
    config: dict | None = None,
) -> dict:
    """
    计算单时刻路表温度。

    Args:
        air_temp: 气温 (°C)
        gti: 全球倾斜辐射 (W/m²), 反映路面真实吸热
        wind_speed: 风速 (m/s)
        color: 路面颜色 (black/gray/light_gray/unknown)
        age_years: 路面使用年数
        config: pavement_model 配置

    Returns:
        {
          "pavement_temp": 路表温度 (°C),
          "air_temp": 气温 (°C),
          "radiation_delta": 辐射升温 (°C),
          "color_delta": 颜色修正 (°C),
          "age_delta": 老化修正 (°C),
          "wind_delta": 风冷修正 (°C),
          "model_version": 模型版本,
          "confidence": 置信度 (HIGH/MEDIUM/LOW)
        }
    """
    if config is None:
        config = load_model_config()

    alpha_rad = config.get("radiation_coefficient", 0.035)
    color_abs = config.get("color_absorption", {}).get(color, 0.78)
    age_table = config.get("age_aging_correction", {0: 0, 5: 0.5, 10: 1.0, 15: 1.2, 20: 1.0})

    # 颜色-辐射交互项: 深色路面同等辐射下吸热更多
    # 归一化辐射: 假设典型中午峰值 800 W/m²
    gti_norm = (gti or 0) / 800.0
    color_delta = color_abs * gti_norm * 2.5  # 颜色吸收率产生的附加升温

    radiation_delta = alpha_rad * (gti or 0)
    age_delta = get_age_correction(age_years, age_table)
    wind_delta = -0.5 * (wind_speed or 0)  # 风冷

    pavement_temp = air_temp + radiation_delta + color_delta + age_delta + wind_delta

    # 置信度
    if color == "unknown" or age_years is None:
        confidence = "LOW"
    elif color in ("black", "gray") and age_years is not None:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    return {
        "pavement_temp": round(pavement_temp, 1),
        "air_temp": round(air_temp, 1),
        "radiation_delta": round(radiation_delta, 2),
        "color_delta": round(color_delta, 2),
        "age_delta": round(age_delta, 2),
        "wind_delta": round(wind_delta, 2),
        "color_used": color,
        "gti_w_m2": gti or 0,
        "model_version": "0.1-shrp-ltpp",
        "confidence": confidence,
    }


def compute_hourly_pavement(weather_data: dict, point_config: dict, model_config: dict | None = None) -> list:
    """
    批量计算 14 天逐小时路表温度。

    Args:
        weather_data: fetch_weather.py 输出
        point_config: monitoring_points.yaml 中该点的配置 (含 color, age_years)
        model_config: 建模配置

    Returns:
        [{time, air_temp, pavement_temp, ...}, ...]
    """
    if model_config is None:
        model_config = load_model_config()

    color = point_config.get("pavement_color", "unknown")
    age = point_config.get("pavement_age_years")

    results = []
    for h in weather_data.get("hourly", []):
        if h.get("temperature_2m") is None:
            continue
        result = compute_pavement_temp(
            air_temp=h["temperature_2m"],
            gti=h.get("global_tilted_irradiance"),
            wind_speed=h.get("wind_speed_10m"),
            color=color,
            age_years=age,
            config=model_config,
        )
        result["time"] = h["time"]
        result["cloud_cover"] = h.get("cloud_cover")
        results.append(result)
    return results


def daily_peak(hourly_results: list) -> list:
    """
    从 hourly 提每天峰值路表温度。
    返回: [{date, peak_pavement_temp, peak_time, peak_air_temp}, ...]
    """
    from collections import defaultdict
    by_date = defaultdict(list)
    for h in hourly_results:
        date = h["time"][:10]
        by_date[date].append(h)

    peaks = []
    for date, hours in by_date.items():
        # 只看高温窗口 (11:00-16:00) 的峰值,更贴近实际风险
        hot_hours = [h for h in hours if 11 <= int(h["time"][11:13]) <= 16]
        if not hot_hours:
            hot_hours = hours
        peak = max(hot_hours, key=lambda x: x["pavement_temp"])
        peaks.append({
            "date": date,
            "peak_pavement_temp": peak["pavement_temp"],
            "peak_time": peak["time"],
            "peak_air_temp": peak["air_temp"],
            "peak_gti": peak.get("gti_w_m2", 0),
        })
    peaks.sort(key=lambda x: x["date"])
    return peaks


# ===== CLI =====
if __name__ == "__main__":
    print("=== model.py 单点测试 ===")
    # 模拟 7-13 那种极端场景: 35°C 气温, GTI 800 W/m², 风速 1
    test = compute_pavement_temp(
        air_temp=35.0,
        gti=800,
        wind_speed=1.0,
        color="unknown",
        age_years=None,
    )
    print("场景1: T_air=35, GTI=800, 风=1, 颜色=unknown, 年限=未知")
    for k, v in test.items():
        print(f"  {k}: {v}")

    test2 = compute_pavement_temp(
        air_temp=28.3,
        gti=372,
        wind_speed=2.1,
        color="black",
        age_years=5,
    )
    print("\n场景2: T_air=28.3, GTI=372, 风=2.1, 颜色=black, 5年 (今天 14:00 真实数据)")
    for k, v in test2.items():
        print(f"  {k}: {v}")
