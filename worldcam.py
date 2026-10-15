#!/usr/bin/env python3
"""
世界窗 WorldCam
---------------
一句话看看世界上任何地方现在什么样。

功能：
  - 公开摄像头实时帧（Windy API 主搜 + SkylineWebcams 兜底）
  - 当前天气（Open-Meteo）
  - 附近野生动物观察记录（iNaturalist）
  - 当地时区与时间

用法：
  python3 worldcam.py "Tokyo"                 # 看一个地方
  python3 worldcam.py "San Francisco" 100     # 100km 半径
  python3 worldcam.py "Tokyo" --radius 100    # 同上
  python3 worldcam.py "Tokyo" --no-wildlife   # 跳过野生动物
  python3 worldcam.py "Tokyo" --list          # 只列摄像头，不下图

依赖：无（只用 Python 标准库，3.8+）
配置：config.json 填 windy_api_key，或设环境变量 WINDY_API_KEY
      离线部署也可用 WORLDCAM_KEY_FILE 指向密钥文件。
"""
import json
import os
import sys
import time
import socket
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SKYLINE_PATH = os.path.join(BASE_DIR, "skyline_cams.json")
TIMEOUT = 30

# 离线部署时可覆盖：WORLDCAM_OUT（图片目录）/ WORLDCAM_KEY_FILE（密钥文件）
OUT_DIR = os.environ.get("WORLDCAM_OUT") or os.path.join(BASE_DIR, "frames")
KEY_FILE = os.environ.get("WORLDCAM_KEY_FILE")

# config.json 里没改的占位符——读到它等于没配
KEY_PLACEHOLDER = "YOUR_WINDY_API_KEY_HERE"

# ---------------------------------------------------------------------------
# 常用中文地名 → 英文映射
# ---------------------------------------------------------------------------
# 实测（2026-10）：Open-Meteo 地理库对中文支持很差——
#   language=en 时「东京」「纽约」直接查不到；
#   language=zh 时会先命中同名小地方（「东京」命中中国小镇）。
# 最稳的方案是查询前先过这张表。不在表里的地名按原文查询，
# 中文可能查不到，遇到就补一行。
CN_PLACE_MAP = {
    # 中国
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou",
    "深圳": "Shenzhen", "成都": "Chengdu", "杭州": "Hangzhou",
    "西安": "Xi'an", "南京": "Nanjing", "武汉": "Wuhan",
    "重庆": "Chongqing", "天津": "Tianjin", "苏州": "Suzhou",
    "青岛": "Qingdao", "厦门": "Xiamen", "大连": "Dalian",
    "哈尔滨": "Harbin", "昆明": "Kunming", "长沙": "Changsha",
    "郑州": "Zhengzhou", "拉萨": "Lhasa", "三亚": "Sanya",
    "桂林": "Guilin", "丽江": "Lijiang", "张家界": "Zhangjiajie",
    "敦煌": "Dunhuang", "乌鲁木齐": "Urumqi",
    "香港": "Hong Kong", "澳门": "Macau", "台北": "Taipei", "高雄": "Kaohsiung",
    # 东亚
    "东京": "Tokyo", "東京": "Tokyo", "大阪": "Osaka", "京都": "Kyoto",
    "札幌": "Sapporo", "首尔": "Seoul", "汉城": "Seoul",
    # 东南亚 / 南亚 / 中东
    "新加坡": "Singapore", "曼谷": "Bangkok", "吉隆坡": "Kuala Lumpur",
    "雅加达": "Jakarta", "马尼拉": "Manila", "河内": "Hanoi",
    "孟买": "Mumbai", "新德里": "New Delhi", "迪拜": "Dubai",
    # 欧洲
    "伦敦": "London", "倫敦": "London", "巴黎": "Paris", "柏林": "Berlin",
    "罗马": "Rome", "米兰": "Milan", "威尼斯": "Venice", "马德里": "Madrid",
    "巴塞罗那": "Barcelona", "阿姆斯特丹": "Amsterdam", "维也纳": "Vienna",
    "布拉格": "Prague", "雅典": "Athens", "莫斯科": "Moscow",
    # 北美
    "纽约": "New York", "紐約": "New York", "洛杉矶": "Los Angeles",
    "旧金山": "San Francisco", "西雅图": "Seattle", "波士顿": "Boston",
    "芝加哥": "Chicago", "华盛顿": "Washington", "迈阿密": "Miami",
    "拉斯维加斯": "Las Vegas", "温哥华": "Vancouver", "多伦多": "Toronto",
    # 其他
    "开罗": "Cairo", "悉尼": "Sydney", "墨尔本": "Melbourne",
    "奥克兰": "Auckland", "伊斯坦布尔": "Istanbul",
    "卡萨布兰卡": "Casablanca", "内罗毕": "Nairobi",
    "圣保罗": "Sao Paulo", "里约热内卢": "Rio de Janeiro",
    "布宜诺斯艾利斯": "Buenos Aires",
}


def load_config():
    """读 config.json；缺 key 时回退环境变量/密钥文件。占位符视为未配置。"""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except (ValueError, OSError):
            cfg = {}

    key = (cfg.get("windy_api_key") or "").strip()
    if not key or key == KEY_PLACEHOLDER:
        key = os.environ.get("WINDY_API_KEY", "").strip()
    if not key and KEY_FILE and os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE) as f:
                key = f.read().strip()
        except OSError:
            key = ""
    return {"windy_api_key": key}


def load_skyline():
    if os.path.exists(SKYLINE_PATH):
        try:
            with open(SKYLINE_PATH) as f:
                return json.load(f)
        except (ValueError, OSError):
            return {}
    return {}


def http_get(url, headers=None, timeout=TIMEOUT, binary=False, retries=2):
    """GET with retry.

    只重试网络层故障（超时、DNS、连接被拒）。HTTP 4xx/5xx 直接抛——
    重试一个 401 只会白等三倍时间。
    """
    h = {"User-Agent": "WorldCam/1.0"}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return data if binary else json.loads(data)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            last = e
            if attempt == retries - 1:
                break
            time.sleep(3)
    raise last if last else RuntimeError("request failed")


def _geocode_search(name, lang="en"):
    """单次地理编码查询，返回候选列表。"""
    q = urllib.parse.urlencode({"name": name, "count": 10, "language": lang})
    try:
        d = http_get(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    except Exception:
        return []
    return d.get("results") or []


def _has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def geocode(place):
    """Open-Meteo geocoding，三级兜底。

    中文地名坑（实测 2026-10）：
      - language=en 时中文基本查不到（「东京」「纽约」都返回空）
      - language=zh 时中文能查到，但会先命中同名小地方
        （「东京」命中中国小镇；繁体「東京」才命中日本东京）
      - 即使查到也可能撞重名（「开罗」命中美国 1733 人的小镇，
        而不是埃及开罗 960 万）
    策略：
      1) 先查 CN_PLACE_MAP 转英文（最可靠，热门城市全覆盖）
      2) 原名直接查（英文名/拼音走这里）
      3) 中文名用 language=zh 兜底
      4) name 精确匹配优先，否则人口降序
    """
    raw = place.strip()
    candidates = []

    mapped = CN_PLACE_MAP.get(raw)
    if mapped:
        candidates = _geocode_search(mapped, "en")

    if not candidates:
        candidates = _geocode_search(raw, "en")

    if not candidates and _has_cjk(raw):
        candidates = _geocode_search(raw, "zh")

    if not candidates:
        return None

    target = (mapped or raw).lower()
    exact = [c for c in candidates if c.get("name", "").lower() == target]
    pool = exact or candidates
    # 人口降序；population 缺失的排后面而不是当 0
    pool = sorted(pool, key=lambda x: x.get("population") or 0, reverse=True)
    loc = pool[0]
    return {
        "name": loc["name"],
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "admin1": loc.get("admin1", ""),
        "country": loc.get("country", ""),
        "population": loc.get("population"),
        "timezone": loc.get("timezone", "UTC"),
    }


def _fmt_offset(hours):
    """输出 UTC+5:30 这种，别再输出 UTC+5。"""
    sign = "+" if hours >= 0 else "-"
    h = abs(hours)
    whole = int(h)
    mins = int(round((h - whole) * 60))
    if mins:
        return f"UTC{sign}{whole}:{mins:02d}"
    return f"UTC{sign}{whole}"


def get_weather(lat, lon):
    """Open-Meteo current weather。"""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current_weather=true&daily=temperature_2m_max,temperature_2m_min"
        f"&forecast_days=1&timezone=auto"
    )
    d = http_get(url)
    cw = d.get("current_weather", {})
    daily = d.get("daily", {})
    # 注意：不能 // 3600——印度 +5:30、尼泊尔 +5:45 会被抹成整点
    offset_hours = d.get("utc_offset_seconds", 0) / 3600.0
    return {
        "temp_c": cw.get("temperature"),
        "wind_kmh": cw.get("wind_speed"),
        "wind_dir": cw.get("wind_direction"),
        "weathercode": cw.get("weathercode"),
        "feels_like": cw.get("apparent_temperature"),
        "t_min": daily.get("temperature_2m_min", [None])[0],
        "t_max": daily.get("temperature_2m_max", [None])[0],
        "local_time": cw.get("time"),
        "tz_name": d.get("timezone", "UTC"),
        "utc_offset_hours": offset_hours,
    }


def find_windy_cameras(lat, lon, radius_km, api_key):
    """Search Windy webcams near coordinates."""
    url = (
        f"https://api.windy.com/webcams/api/v3/webcams"
        f"?nearby={lat},{lon},{radius_km}&limit=20"
    )
    d = http_get(url, headers={"x-windy-api-key": api_key})
    cams = [c for c in d.get("webcams", []) if c.get("status") == "active"]
    cams.sort(key=lambda c: c.get("viewCount") or 0, reverse=True)
    return cams


def save_frame(data, stem):
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{stem}.jpg")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def download_windy_image(cam_id, api_key):
    """Download the latest frame from a Windy webcam."""
    url = (
        f"https://api.windy.com/webcams/api/v3/webcams/{cam_id}"
        f"?include=images,location,urls"
    )
    d = http_get(url, headers={"x-windy-api-key": api_key})
    img_url = d.get("images", {}).get("current", {}).get("preview", "")
    if not img_url:
        return None, d
    data = http_get(img_url, binary=True)
    return save_frame(data, f"windy_{cam_id}"), d


def find_skyline_cam(place_name, skyline_data):
    """Match a location against pre-mapped SkylineWebcams IDs."""
    p = (place_name or "").lower().strip()
    if not p:
        return None
    for key, val in skyline_data.items():
        if key.lower() == p:
            return val
    # 双向子串只在两边都够长时才信，否则 "rome" 会命中 "rome airport"
    if len(p) >= 5:
        for key, val in skyline_data.items():
            if len(key) >= 5 and (key.lower() in p or p in key.lower()):
                return val
        for key, val in skyline_data.items():
            for alias in val.get("aliases", []):
                a = alias.lower()
                if len(a) >= 5 and (a in p or p in a):
                    return val
    return None


def download_skyline_image(cam_data):
    """Download the current frame from SkylineWebcams CDN."""
    cam_id = cam_data["id"]
    for pattern in [f"https://cdn.skylinewebcams.com/social{cam_id}.jpg",
                    f"https://cdn.skylinewebcams.com/live{cam_id}.jpg",
                    f"https://cdn.skylinewebcams.com/live{cam_id}.webp"]:
        try:
            data = http_get(pattern, binary=True, timeout=15, retries=1)
            if len(data) > 500:
                return save_frame(data, f"skyline_{cam_id}")
        except Exception:
            continue
    return None


def get_wildlife(lat, lon, radius_km=50):
    """Recent wildlife observations from iNaturalist.

    两个坑（都踩过）：
      - 经度参数是 `lng` 不是 `lon`。写 `lon` 的话 iNat 会静默忽略整个
        位置过滤，返回全球记录（total_results 两亿多），不报错。
      - `radius` 单位是 km 不是米。上限 500km。
    """
    radius_km = max(1, min(int(radius_km), 500))
    url = (
        f"https://api.inaturalist.org/v1/observations"
        f"?lat={lat}&lng={lon}"
        f"&radius={radius_km}&per_page=3"
        f"&order_by=observed_on&quality_grade=research"
    )
    try:
        d = http_get(url, timeout=15)
    except Exception:
        return []
    results = []
    for obs in d.get("results", []):
        taxon = obs.get("taxon", {})
        results.append({
            "species": taxon.get("preferred_common_name")
                        or taxon.get("scientific_name") or "Unknown",
            "observed_on": obs.get("observed_on", ""),
            "place": obs.get("place_guess", ""),
            "photo": obs.get("photo", {}).get("square", ""),
            "observer": obs.get("user", {}).get("login", ""),
        })
    return results[:3]


def list_cameras(geo, radius, windy_key):
    cams = []
    if windy_key:
        for c in find_windy_cameras(geo["latitude"], geo["longitude"],
                                   radius, windy_key):
            cams.append({
                "provider": "Windy Webcams",
                "title": c.get("title", ""),
                "views": c.get("viewCount") or 0,
                "status": c.get("status", ""),
                "id": c.get("webcamId"),
                "last_updated": c.get("lastUpdatedOn", ""),
            })
    if not cams:
        info = find_skyline_cam(geo["name"], load_skyline())
        if info:
            cams.append({
                "provider": "SkylineWebcams",
                "title": info.get("name", str(info.get("id"))),
                "views": None,
                "status": "unknown",
                "id": info.get("id"),
                "url": info.get("page_url", ""),
            })
    return cams


def main():
    args = sys.argv[1:]
    no_wildlife = False
    list_only = False
    radius = 50
    place = "Tokyo"
    positional = []

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--no-wildlife":
            no_wildlife = True
        elif a == "--list":
            list_only = True
        elif a == "--radius" and i + 1 < len(args):
            try:
                radius = int(args[i + 1])
            except ValueError:
                pass
            i += 1
        else:
            positional.append(a)
        i += 1

    if positional:
        place = positional[0]
    if len(positional) > 1 and positional[1].isdigit():
        radius = int(positional[1])

    radius = max(1, min(radius, 500))

    cfg = load_config()
    windy_key = cfg["windy_api_key"]

    t0 = time.time()
    result = {"query": place, "radius_km": radius}

    geo = geocode(place)
    if not geo:
        result["error"] = f"Cannot geocode '{place}'"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    result["location"] = geo

    if list_only:
        result["cameras"] = list_cameras(geo, radius, windy_key)
        result["elapsed_sec"] = round(time.time() - t0, 1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    try:
        result["weather"] = get_weather(geo["latitude"], geo["longitude"])
    except Exception as e:
        result["weather"] = {"error": str(e)}

    image_path = None
    cam_source = None
    if windy_key:
        try:
            cams = find_windy_cameras(geo["latitude"], geo["longitude"],
                                      radius, windy_key)
            if cams:
                best = cams[0]
                img, detail = download_windy_image(best["webcamId"], windy_key)
                if img:
                    image_path = img
                    cam_source = {
                        "provider": "Windy Webcams",
                        "title": best.get("title", ""),
                        "views": best.get("viewCount", 0),
                        "last_updated": best.get("lastUpdatedOn", ""),
                        "url": detail.get("urls", {}).get("detail", ""),
                    }
        except Exception as e:
            cam_source = {"error": f"Windy: {e}"}
    else:
        cam_source = {"note": "No Windy API key configured; "
                              "set WINDY_API_KEY or fill config.json"}

    if not image_path:
        cam_info = find_skyline_cam(geo["name"], load_skyline())
        if cam_info:
            img = download_skyline_image(cam_info)
            if img:
                image_path = img
                cam_source = {
                    "provider": "SkylineWebcams",
                    "title": cam_info.get("name", str(cam_info.get("id"))),
                    "url": cam_info.get("page_url", ""),
                }

    if image_path:
        result["camera"] = cam_source
        result["camera_image"] = {
            "local_path": image_path,
            "size_bytes": os.path.getsize(image_path)
                          if os.path.exists(image_path) else 0,
        }
    else:
        result["camera"] = cam_source or {"note": "No camera found"}

    if not no_wildlife:
        result["wildlife"] = get_wildlife(geo["latitude"], geo["longitude"],
                                          radius)

    w = result.get("weather", {})
    offset = w.get("utc_offset_hours")
    if offset is None:
        offset = 0.0
    result["local_time"] = datetime.now(
        timezone(timedelta(hours=offset))).strftime("%Y-%m-%d %H:%M")
    result["timezone"] = _fmt_offset(offset)

    result["elapsed_sec"] = round(time.time() - t0, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
