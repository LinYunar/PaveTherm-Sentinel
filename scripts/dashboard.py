#!/usr/bin/env python3
"""
dashboard.py - 生成 PaveTherm Sentinel 可视化 dashboard

设计: Linear/Vercel 深色风
  - 顶部: 统计卡片 (总点位/红/橙/黄/绿)
  - 中部左 60%: Leaflet 瓦片地图 (OSM tiles), markers 按预警等级着色
  - 中部右 40%: ECharts 14天峰值热图
  - 底部: 点位列表表格 (双单位, 颜色 tag)

输出: ~/.hermes/skills/pavetherm-sentinel/dashboards/index.html (单文件, 含数据)
"""
import sys
import json
import yaml
from pathlib import Path
from datetime import datetime

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from geocode import geocode
from fetch_weather import fetch_open_meteo
from model import compute_pavement_temp, compute_hourly_pavement, daily_peak
from alert import classify_level

DASHBOARD_DIR = SKILL_ROOT / "dashboards"
DASHBOARD_DIR.mkdir(exist_ok=True)


def collect_all_points_data() -> dict:
    """收集所有监测点的实时+14天数据"""
    data_path = SKILL_ROOT / "data" / "monitoring_points.yaml"
    with open(data_path, "r", encoding="utf-8") as f:
        db = yaml.safe_load(f)

    pts = db.get("monitoring_points", [])
    out_pts = []

    for p in pts:
        if not p.get("geocoded") or p.get("lat") is None:
            continue
        weather = fetch_open_meteo(p["lat"], p["lon"])
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

        lvl = classify_level(cp["pavement_temp"], p.get("warning_thresholds"))

        out_pts.append({
            "id": p["id"],
            "name": p["name"],
            "lat": p["lat"],
            "lon": p["lon"],
            "display": p.get("geocode_display") or p.get("location_input"),
            "pavement_color": p.get("pavement_color", "unknown"),
            "pavement_age_years": p.get("pavement_age_years"),
            "current_pavement_c": cp["pavement_temp"],
            "current_pavement_f": round(cp["pavement_temp"] * 9 / 5 + 32, 1),
            "current_air_c": cp["air_temp"],
            "alert_level": lvl["level"],
            "alert_label": lvl["label"],
            "peak_14d_max_c": max(pk["peak_pavement_temp"] for pk in peaks),
            "peak_14d": [{"date": pk["date"], "peak_c": pk["peak_pavement_temp"],
                          "peak_f": round(pk["peak_pavement_temp"] * 9 / 5 + 32, 1),
                          "time": pk["peak_time"][11:16],
                          "level": classify_level(pk["peak_pavement_temp"])["level"]}
                         for pk in peaks],
        })

    summary = {
        "total": len(out_pts),
        "red": sum(1 for p in out_pts if p["alert_level"] == "red"),
        "orange": sum(1 for p in out_pts if p["alert_level"] == "orange"),
        "yellow": sum(1 for p in out_pts if p["alert_level"] == "yellow"),
        "green": sum(1 for p in out_pts if p["alert_level"] == "green"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"summary": summary, "points": out_pts}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🌡️ PaveTherm Sentinel · 沥青路表温度监测</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
:root {
  --bg-primary: #0a0e14;
  --bg-secondary: #131820;
  --bg-card: #1a2028;
  --bg-hover: #232b36;
  --border: #2a3441;
  --text-primary: #e6edf3;
  --text-secondary: #8b96a5;
  --text-muted: #5c6773;
  --accent: #5e9bff;
  --green: #3fb950;
  --yellow: #d29922;
  --orange: #f0883e;
  --red: #f85149;
  --shadow: 0 4px 16px rgba(0,0,0,0.4);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.5;
  min-height: 100vh;
}
.header {
  background: linear-gradient(135deg, #1a2028 0%, #131820 100%);
  border-bottom: 1px solid var(--border);
  padding: 24px 32px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.header h1 {
  font-size: 22px;
  font-weight: 600;
  background: linear-gradient(90deg, #5e9bff, #d29922);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.header .meta { color: var(--text-muted); font-size: 13px; }
.stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  padding: 24px 32px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
}
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 20px;
  position: relative;
  overflow: hidden;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 3px;
  height: 100%;
  background: var(--accent);
}
.stat-card.red::before { background: var(--red); }
.stat-card.orange::before { background: var(--orange); }
.stat-card.yellow::before { background: var(--yellow); }
.stat-card.green::before { background: var(--green); }
.stat-card .label { font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .value { font-size: 32px; font-weight: 700; margin-top: 4px; }
.stat-card .value.red { color: var(--red); }
.stat-card .value.orange { color: var(--orange); }
.stat-card .value.yellow { color: var(--yellow); }
.stat-card .value.green { color: var(--green); }
.main {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 16px;
  padding: 24px 32px;
}
.panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: var(--shadow);
}
.panel-header {
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.panel-header h2 { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.panel-header .tag { font-size: 11px; padding: 2px 8px; border-radius: 4px; background: var(--bg-hover); color: var(--text-secondary); }
#map { height: 520px; background: #0a0e14; }
.table-panel { padding: 0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead { background: var(--bg-secondary); }
th { text-align: left; padding: 12px 16px; font-weight: 500; color: var(--text-secondary); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
td { padding: 14px 16px; border-bottom: 1px solid var(--border); }
tbody tr:hover { background: var(--bg-hover); }
.level-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}
.level-badge.green { background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid rgba(63,185,80,0.3); }
.level-badge.yellow { background: rgba(210,153,34,0.15); color: var(--yellow); border: 1px solid rgba(210,153,34,0.3); }
.level-badge.orange { background: rgba(240,136,62,0.15); color: var(--orange); border: 1px solid rgba(240,136,62,0.3); }
.level-badge.red { background: rgba(248,81,73,0.15); color: var(--red); border: 1px solid rgba(248,81,73,0.3); }
.temp-cell { font-variant-numeric: tabular-nums; }
.temp-cell .c { font-weight: 600; }
.temp-cell .f { color: var(--text-muted); font-size: 11px; margin-left: 4px; }
.name-cell { cursor: pointer; }
.name-cell:hover { color: var(--accent); }
.footer {
  padding: 16px 32px;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
  background: var(--bg-secondary);
}
.dot {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}
.dot.green { background: var(--green); }
.dot.yellow { background: var(--yellow); }
.dot.orange { background: var(--orange); }
.dot.red { background: var(--red); }
</style>
</head>
<body>
<div class="header">
  <h1>🌡️ PaveTherm Sentinel · 沥青路表温度监测</h1>
  <div class="meta">生成时间: <span id="gen-time"></span> · 数据源: Open-Meteo + 高德地图</div>
</div>

<div class="stats">
  <div class="stat-card">
    <div class="label">总监测点</div>
    <div class="value" id="stat-total">-</div>
  </div>
  <div class="stat-card green">
    <div class="label"><span class="dot green"></span>绿色 (正常)</div>
    <div class="value green" id="stat-green">-</div>
  </div>
  <div class="stat-card yellow">
    <div class="label"><span class="dot yellow"></span>黄色 (注意)</div>
    <div class="value yellow" id="stat-yellow">-</div>
  </div>
  <div class="stat-card orange">
    <div class="label"><span class="dot orange"></span>橙色 (警戒)</div>
    <div class="value orange" id="stat-orange">-</div>
  </div>
  <div class="stat-card red">
    <div class="label"><span class="dot red"></span>红色 (危险)</div>
    <div class="value red" id="stat-red">-</div>
  </div>
</div>

<div class="main">
  <div class="panel">
    <div class="panel-header">
      <h2>📍 监测点地图</h2>
      <span class="tag">Leaflet + OpenStreetMap</span>
    </div>
    <div id="map"></div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <h2>📈 14 天路表温度预测</h2>
      <span class="tag">ECharts</span>
    </div>
    <div id="chart" style="height: 520px;"></div>
  </div>
</div>

<div class="main" style="grid-template-columns: 1fr;">
  <div class="panel table-panel">
    <div class="panel-header">
      <h2>📋 监测点列表</h2>
      <span class="tag" id="pts-count">-</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>等级</th>
          <th>监测点</th>
          <th>路面</th>
          <th>当前路表</th>
          <th>14天峰值</th>
          <th>预警</th>
        </tr>
      </thead>
      <tbody id="pts-tbody"></tbody>
    </table>
  </div>
</div>

<div class="footer">
  PaveTherm Sentinel v0.1 · 模型: SHRP/LTPP 经验公式 (参数偏保守) · 14 天预报每小时更新
</div>

<script>
// ====== 数据 (由 dashboard.py 注入) ======
const DATA = __DATA_PLACEHOLDER__;

// ====== 统计卡片 ======
document.getElementById('stat-total').textContent = DATA.summary.total;
document.getElementById('stat-green').textContent = DATA.summary.green;
document.getElementById('stat-yellow').textContent = DATA.summary.yellow;
document.getElementById('stat-orange').textContent = DATA.summary.orange;
document.getElementById('stat-red').textContent = DATA.summary.red;
document.getElementById('gen-time').textContent = DATA.summary.generated_at;
document.getElementById('pts-count').textContent = `共 ${DATA.summary.total} 个点位`;

// ====== 地图 ======
const map = L.map('map', {zoomControl: true}).setView([30.65, 104.07], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '© OpenStreetMap'
}).addTo(map);

const levelColors = {green: '#3fb950', yellow: '#d29922', orange: '#f0883e', red: '#f85149'};
const markers = {};
DATA.points.forEach(p => {
  const color = levelColors[p.alert_level];
  const html = `<div style="
    background: ${color};
    width: 28px; height: 28px;
    border-radius: 50%;
    border: 3px solid #fff;
    box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 700; font-size: 11px;
    font-family: -apple-system, sans-serif;
  ">${p.current_pavement_c.toFixed(0)}</div>`;
  const icon = L.divIcon({html, className: 'pt-marker', iconSize: [28, 28], iconAnchor: [14, 14]});
  const marker = L.marker([p.lat, p.lon], {icon}).addTo(map);
  marker.bindPopup(`
    <div style="font-family: -apple-system; min-width: 200px;">
      <div style="font-weight: 600; font-size: 14px; margin-bottom: 6px;">${p.name}</div>
      <div style="color: #666; font-size: 11px; margin-bottom: 8px;">${p.display}</div>
      <div style="font-size: 12px;">
        当前路表: <b style="color: ${color};">${p.current_pavement_c}°C / ${p.current_pavement_f}°F</b><br>
        14天峰值: ${p.peak_14d_max_c}°C<br>
        等级: <span style="color: ${color};">${p.alert_label}</span>
      </div>
    </div>
  `);
  markers[p.id] = marker;
});

// 自适应 bounds
if (DATA.points.length > 0) {
  const bounds = L.latLngBounds(DATA.points.map(p => [p.lat, p.lon]));
  map.fitBounds(bounds.pad(0.2));
}

// ====== 14天图表 ======
const chart = echarts.init(document.getElementById('chart'), 'dark');
const allDates = [...new Set(DATA.points.flatMap(p => p.peak_14d.map(d => d.date)))].sort();
const series = DATA.points.map(p => ({
  name: p.name,
  type: 'line',
  smooth: true,
  symbol: 'circle',
  symbolSize: 6,
  data: allDates.map(d => {
    const day = p.peak_14d.find(x => x.date === d);
    return day ? day.peak_c : null;
  }),
  itemStyle: {color: levelColors[p.alert_level]},
  lineStyle: {width: 2, color: levelColors[p.alert_level]},
  emphasis: {focus: 'series'},
  markLine: p.alert_level === 'red' ? {
    silent: true,
    symbol: 'none',
    lineStyle: {color: '#f85149', type: 'dashed', width: 1},
    data: [{yAxis: 65, label: {formatter: '红警 65°C', color: '#f85149', fontSize: 10}}]
  } : undefined,
}));
chart.setOption({
  backgroundColor: 'transparent',
  tooltip: {trigger: 'axis', axisPointer: {type: 'cross'}, valueFormatter: v => v ? `${v}°C` : '-'},
  legend: {type: 'scroll', top: 0, textStyle: {color: '#8b96a5', fontSize: 11}},
  grid: {left: 50, right: 20, top: 40, bottom: 50},
  xAxis: {type: 'category', data: allDates, axisLabel: {color: '#8b96a5', fontSize: 10, rotate: 30}, axisLine: {lineStyle: {color: '#2a3441'}}},
  yAxis: {type: 'value', name: '°C', nameTextStyle: {color: '#8b96a5'}, axisLabel: {color: '#8b96a5', formatter: '{value}°C'}, splitLine: {lineStyle: {color: '#2a3441'}}, max: 90},
  series: series,
});

// ====== 表格 ======
const tbody = document.getElementById('pts-tbody');
const levelOrder = {red:0, orange:1, yellow:2, green:3};
[...DATA.points].sort((a,b) => levelOrder[a.alert_level] - levelOrder[b.alert_level] || b.current_pavement_c - a.current_pavement_c).forEach(p => {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><span class="level-badge ${p.alert_level}">${p.alert_label}</span></td>
    <td class="name-cell" data-id="${p.id}">
      <div style="font-weight: 500;">${p.name}</div>
      <div style="color: var(--text-muted); font-size: 11px;">${p.display || ''}</div>
    </td>
    <td>${p.pavement_color} · ${p.pavement_age_years || '?'}年</td>
    <td class="temp-cell"><span class="c" style="color: ${levelColors[p.alert_level]};">${p.current_pavement_c}°C</span><span class="f">/ ${p.current_pavement_f}°F</span></td>
    <td class="temp-cell"><span class="c">${p.peak_14d_max_c}°C</span><span class="f">/ ${(p.peak_14d_max_c*9/5+32).toFixed(1)}°F</span></td>
    <td><span class="level-badge ${p.alert_level}">${p.alert_level.toUpperCase()}</span></td>
  `;
  tbody.appendChild(tr);
});
document.querySelectorAll('.name-cell').forEach(el => {
  el.addEventListener('click', () => {
    const id = el.dataset.id;
    if (markers[id]) { map.setView(markers[id].getLatLng(), 15); markers[id].openPopup(); }
  });
});

window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>
"""


def build_dashboard() -> Path:
    data = collect_all_points_data()
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False))
    out_path = DASHBOARD_DIR / "index.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


# ===== CLI =====
def load_summary() -> dict:
    data = collect_all_points_data()
    return data["summary"]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="生成后启动本地 http server")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    print("[dashboard] 正在收集数据...")
    path = build_dashboard()
    print(f"[dashboard] ✅ 生成: {path}")
    print(f"[dashboard] 摘要: {json.dumps(load_summary(), ensure_ascii=False, indent=2)}")

    if args.serve:
        import http.server
        import socketserver
        import os
        os.chdir(DASHBOARD_DIR)
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", args.port), handler) as httpd:
            print(f"[dashboard] 🌐 http://localhost:{args.port}/index.html")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n[dashboard] stopped")
