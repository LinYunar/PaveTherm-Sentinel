# API 参考 — Open-Meteo + 高德

## Open-Meteo Forecast API

**Endpoint**: `https://api.open-meteo.com/v1/forecast`

**无需 API key, 容器内可达(实测 200, ~1s)**。

### 推荐请求参数(本 skill 使用)

```python
params = {
    "latitude": lat,                     # 必需
    "longitude": lon,                    # 必需
    "current_weather": "true",           # 当前天气
    "forecast_days": 14,                 # 1-16
    "timezone": "Asia/Shanghai",         # 本地化时间戳
    "hourly": ",".join([
        "temperature_2m",                # 气温
        "global_tilted_irradiance",      # ⭐ GTI - 瓦片辐射, 路面真实吸热
        "cloud_cover",                   # 云量 (%)
        "wind_speed_10m",                # 风速
    ]),
    "daily": ",".join([
        "temperature_2m_max",            # 日最高气温
        "temperature_2m_min",            # 日最低气温
        "sunrise",                       # 日出
        "sunset",                        # 日落
        "precipitation_sum",             # 降水
    ]),
}
```

### 响应关键字段

```json
{
  "latitude": 30.65,
  "longitude": 104.15,
  "elevation": 518.0,                    # 海拔 (m)
  "timezone": "Asia/Shanghai",
  "current_weather": {
    "time": "2026-06-30T09:30",
    "temperature": 24.9,                 # ⚠️ 字段名 temperature, 不是 temperature_2m
    "windspeed": 2.1,                    # ⚠️ windspeed, 不是 wind_speed_10m
    "winddirection": 239,
    "weathercode": 3,
    "is_day": 1
  },
  "hourly": {
    "time": ["2026-06-30T00:00", ...],   # ISO 8601, 1h 步长
    "temperature_2m": [24.3, 23.9, ...],
    "global_tilted_irradiance": [0.0, 0.0, ...],  # 白天峰值 400-900 W/m²
    "cloud_cover": [100, 100, ...],
    "wind_speed_10m": [...]
  },
  "daily": {
    "time": ["2026-06-30", ...],
    "temperature_2m_max": [29.2, ...],
    "sunrise": ["2026-06-30T06:00", ...],
    "sunset": ["2026-06-30T20:00", ...]
  }
}
```

### 字段命名不一致陷阱

⚠️ `current_weather` 用 `temperature` / `windspeed`,而 `hourly` 用 `temperature_2m` / `wind_speed_10m`。**同一变量,两套命名**,代码要分开处理。

### GTI 典型范围(成都,夏季实测样本)

| 时段 | GTI (W/m²) | 备注 |
|---|---|---|
| 夜晚 22:00-05:00 | 0 | |
| 早晨 08:00 | 50-150 | 太阳斜射 |
| 中午 12:00-14:00 | 300-450 | 阴天 |
| 中午 12:00-14:00 | 700-900 | 晴天 |
| 傍晚 17:00 | 200-400 | 仍较强 |

---

## 高德地图 Web API — 地理编码

**Endpoint**: `https://restapi.amap.com/v3/geocode/geo`

**需 API key, 容器内可达(实测 200, ~0.2s)**。
免费配额 6000 次/天, 添加监测点场景完全够用。

### 请求

```python
params = {
    "key": api_key,
    "address": query,                   # 道路名/POI/地址
    "city": "",                          # 不传则全国搜
    "output": "json",
}
```

### 响应

```json
{
  "status": "1",                        // "1" 成功, "0" 失败
  "info": "OK",
  "geocodes": [{
    "formatted_address": "四川省成都市成华区成渝立交",
    "location": "104.151602,30.634401", // ⚠️ "lng,lat" 经度在前
    "level": "道路",                    // 关键置信度信号
    "adcode": "510108"                  // 行政区代码
  }]
}
```

### 错误码速查

| status | info | 含义 |
|---|---|---|
| 0 | INVALID_USER_KEY | key 无效 |
| 0 | USER_KEY_RECYCLED | key 被回收 |
| 0 | CUQPS_HAS_EXCEEDED_THE_LIMIT | QPS 超限 |
| 0 | DAILY_QUERY_OVER_LIMIT | 日配额超 |
| 1 | OK | 成功 |

### level 字段到置信度映射(本 skill 用法)

```python
def _amap_confidence(level: str, raw: dict) -> float:
    if level in ("道路名", "交叉路口", "桥", "高速"):
        return 0.95
    if level in ("地名地址信息", "兴趣点", "门牌号", "公交站"):
        return 0.85
    if level in ("省/直辖市/特别行政区/自治区", "市/区/县", "商圈"):
        return 0.60
    return 0.70
```

**实测样本**: `成都三环路成渝立交` → level=道路, conf=0.70(脚本中按 0.7 返回,因为 importance 默认 0.5)。`返回 0.7 是个保守低估`,实操命中度对道路名够用。

### 容器内网络可达性实测(2026-06-30)

| API | 状态 | 延迟 |
|---|---|---|
| Open-Meteo | ✅ | ~1.1s |
| 高德 restapi | ✅ | ~0.2s |
| 百度 map api | ✅ | ~0.13s |
| 腾讯 map api | ✅ | ~0.15s |
| 飞书 open-apis | ✅ | ~0.08s |
| **Nominatim (OSM)** | ❌ **不可达** (curl 101 Network unreachable) | timeout |
| Tavily search | ❌ 401 Unauthorized | (key 过期) |

**结论**: 国内道路/POI 监测点,**高德是主路**。Nominatim 只能海外兜底(基本用不到)。