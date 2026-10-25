import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ==================== 1. 環境變數與全域設定 ====================
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

live_mushrooms = []
notified_ids = set()

# 地名座標快取
LOCATION_COORD_CACHE = {
    "kolkata": (22.572646, 88.363895),
    "加爾各答": (22.572646, 88.363895),
    "philadelphia": (39.952584, -75.165222),
    "費城": (39.952584, -75.165222),
    "valparaiso": (-33.047238, -71.612688),
    "瓦爾帕萊索": (-33.047238, -71.612688),
    "david": (8.4273, -82.4309),
    "戴維": (8.4273, -82.4309),
    "sucre": (-19.047725, -65.259431),
    "蘇克雷": (-19.047725, -65.259431),
    "tegucigalpa": (14.072275, -87.192136),
    "德古西加巴": (14.072275, -87.192136),
    "pecs": (46.0727, 18.2323),
    "佩奇": (46.0727, 18.2323),
    "managua": (12.114993, -86.236174),
    "馬拿瓜": (12.114993, -86.236174),
    "manila": (14.599512, 120.984222),
    "馬尼拉": (14.599512, 120.984222),
    "da nang": (16.054407, 108.202167),
    "峴港": (16.054407, 108.202167),
    "houston": (29.760427, -95.369803),
    "休士頓": (29.760427, -95.369803),
    "salto": (-31.383333, -57.966667),
    "薩爾托": (-31.383333, -57.966667),
    "portland": (45.5152, -122.6784),
    "波特蘭": (45.5152, -122.6784)
}

LEVEL_MAP = {
    3: "大蘑菇",
    4: "巨大蘑菇"
}

TYPE_MAP = {
    "2": "紅色", "6": "黃色", "3": "灰色", "8": "紫色",
    "7": "白色", "9": "粉紅色", "5": "藍色",
    "10": "神秘活動", "19": "活動特殊",
    "17": "電", "12": "水", "11": "火", "13": "水晶",
    "18": "毒", "ice": "冰藍", "26": "冰藍",
    "event": "神秘活動", "mystery": "神秘活動"
}

# 純元素目標白名單
TARGET_SPECIAL_TYPES = {"11", "12", "13", "17", "18", "26", "ice"}

# ==================== 2. 地理位置解析與概略座標計算 ====================
def extract_location_text(item):
    country = str(item.get("location_country") or "").strip()
    city = str(item.get("location_city") or "").strip()

    if country and city:
        return f"{country}-{city}"
    elif country:
        return country
    elif city:
        return city

    for key in ["location", "place", "area", "address"]:
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    return "未知區域"

def geocode_location_to_coords(location_text):
    if not location_text or location_text == "未知區域":
        return None, None

    if location_text in LOCATION_COORD_CACHE:
        return LOCATION_COORD_CACHE[location_text]

    english_part = re.sub(r'[\u4e00-\u9fff\-]', ' ', location_text).strip()
    english_part = re.sub(r'\s+', ' ', english_part).lower()

    chinese_part = re.sub(r'[a-zA-Z\-]', ' ', location_text).strip()
    chinese_part = re.sub(r'\s+', ' ', chinese_part)

    # 查離線字典
    if english_part and english_part in LOCATION_COORD_CACHE:
        res = LOCATION_COORD_CACHE[english_part]
        LOCATION_COORD_CACHE[location_text] = res
        return res

    if chinese_part:
        for p in chinese_part.split():
            if p in LOCATION_COORD_CACHE:
                res = LOCATION_COORD_CACHE[p]
                LOCATION_COORD_CACHE[location_text] = res
                return res

    # 快速線上查詢 (Open-Meteo)
    search_terms = []
    if len(english_part) >= 3:
        search_terms.append(english_part)
    if chinese_part:
        search_terms.append(chinese_part.split()[-1])

    for term in search_terms:
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search"
            resp = requests.get(url, params={"name": term, "count": 1, "language": "zh"}, timeout=2.0)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    lat = round(float(results[0]["latitude"]), 6)
                    lng = round(float(results[0]["longitude"]), 6)
                    LOCATION_COORD_CACHE[location_text] = (lat, lng)
                    return lat, lng
        except Exception:
            pass

    LOCATION_COORD_CACHE[location_text] = (None, None)
    return None, None

# ==================== 3. Discord Webhook 發送函式 ====================
def send_discord_notification(mushroom):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL，跳過推播。")
        return

    tz_tw = timezone(timedelta(hours=8))
    now_tw = datetime.now(tz_tw)
    time_str = now_tw.strftime("%Y-%m-%d %H:%M:%S")

    color = 0xF1C40F if mushroom.get('level') == 4 else 0x3498DB

    if mushroom.get('lat') and mushroom.get('lat') != "未提供":
        coord_text = f"{mushroom['lat']}, {mushroom['lng']}"
        gmaps_text = f"[點此前往 Google 地圖]({mushroom['gmaps']})"
    else:
        coord_text = "🔒 原站已隱藏 GPS (無法估算座標)"
        gmaps_text = "[點此前往雷達網站](https://mush.odyliao.cc/)"

    embed_data = {
        "title": f"🚨 發現目標蘑菇：{mushroom['title']}",
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [
            {"name": "🍄 等級與種類", "value": mushroom['title'], "inline": True},
            {"name": "⏰ 通報時間", "value": time_str, "inline": True},
            {"name": "📍 所在區域", "value": f"**{mushroom['location']}**", "inline": False},
            {"name": "🌐 座標", "value": coord_text, "inline": False},
            {"name": "🗺️ 地圖導航", "value": gmaps_text, "inline": False}
        ],
        "footer": {
            "text": "皮克敏雷達即時通報"
        }
    }

    payload = {
        "username": "皮克敏雷達管家",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/616/616490.png",
        "embeds": [embed_data]
    }

    for attempt in range(3):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
            if resp.status_code in [200, 204]:
                print(f"✅ 已成功推播至 Discord: {mushroom['title']} ({mushroom['location']})")
                time.sleep(1)
                return
            elif resp.status_code == 429:
                wait_sec = resp.json().get("retry_after", 1.5)
                time.sleep(float(wait_sec) + 0.2)
            else:
                break
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")
            break

# ==================== 4. 點位同步與通報任務 ====================
def fetch_and_notify_mushrooms():
    global live_mushrooms, notified_ids
    print("📡 開始同步 mush.odyliao.cc 點位...")

    api_url = "https://mush.odyliao.cc/api/mushrooms"
    # 與網頁完全一致的即時參數：抓取最近更新的未滿 5 人點位
    params = {
        "limit": "300",
        "levels": "3,4",
        "sort": "discovered-desc",
        "under_five": "1"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://mush.odyliao.cc/",
        "Cache-Control": "no-cache"
    }

    try:
        res = requests.get(api_url, params=params, headers=headers, timeout=10)
        print(f"📡 API 狀態碼: {res.status_code}, 回傳長度: {len(res.text)}")

        if res.status_code == 200:
            raw_list = res.json().get("mushrooms", [])
            parsed_list = []

            for item in raw_list:
                m_id = str(item.get("id"))
                m_level = item.get("level")
                m_type = str(item.get("type", ""))

                location_name = extract_location_text(item)

                raw_lat = item.get("lat") or item.get("latitude")
                raw_lng = item.get("lng") or item.get("longitude")

                has_real_gps = (raw_lat is not None and raw_lng is not None and 
                                str(raw_lat).strip() not in ["", "None"] and 
                                str(raw_lng).strip() not in ["", "None"])

                if has_real_gps:
                    final_lat, final_lng = raw_lat, raw_lng
                    is_exact = True
                else:
                    approx_lat, approx_lng = geocode_location_to_coords(location_name)
                    final_lat, final_lng = approx_lat, approx_lng
                    is_exact = False

                type_name = TYPE_MAP.get(m_type, f"種類{m_type}")
                level_name = LEVEL_MAP.get(m_level, f"等級{m_level}")
                title = f"{type_name} {level_name}"

                lat_str = str(final_lat) if final_lat is not None else "未提供"
                lng_str = str(final_lng) if final_lng is not None else "未提供"
                gmaps_url = f"https://www.google.com/maps/search/?api=1&query={final_lat},{final_lng}" if final_lat and final_lng else "https://mush.odyliao.cc/"

                m_obj = {
                    "id": m_id,
                    "title": title,
                    "level": m_level,
                    "type": m_type,
                    "location": location_name,
                    "lat": lat_str,
                    "lng": lng_str,
                    "is_exact_gps": is_exact,
                    "gmaps": gmaps_url
                }
                parsed_list.append(m_obj)

                # 【推播過濾】：
                # 排除神秘活動與活動特殊菇 (10, 19, event, mystery)
                # 僅通報：非活動巨大菇 或 純元素大菇 (電、水、火、水晶、毒、冰藍)
                is_excluded = (m_type in ["10", "19", "event", "mystery"])
                is_target = not is_excluded and (m_level == 4 or (m_level == 3 and m_type in TARGET_SPECIAL_TYPES))

                # 即時推播：新出點位立即送出
                if len(notified_ids) > 0 and is_target and m_id not in notified_ids:
                    notified_ids.add(m_id)
                    try:
                        send_discord_notification(m_obj)
                    except Exception as notify_err:
                        print(f"個別推播發送失敗: {notify_err}")
                else:
                    notified_ids.add(m_id)

            live_mushrooms = parsed_list
            print(f"資料更新完成，共掌握 {len(live_mushrooms)} 朵蘑菇。")
        else:
            print(f"API 回應異常，代碼：{res.status_code}")
    except Exception as e:
        print(f"抓取異常: {e}")

# ==================== 5. 路由設定與防休眠 ====================
@app.route("/", methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "message": "Pikmin Bloom Discord Bot is Running!",
        "cached_mushrooms": len(live_mushrooms)
    }), 200

@app.route("/test_discord", methods=['GET'])
def test_discord():
    loc_test = "美國-休士頓 Houston"
    lat, lng = geocode_location_to_coords(loc_test)
    test_obj = {
        "title": "冰藍 大蘑菇",
        "level": 3,
        "location": loc_test,
        "lat": str(lat) if lat else "29.760427",
        "lng": str(lng) if lng else "-95.369803",
        "is_exact_gps": False,
        "gmaps": f"https://www.google.com/maps/search/?api=1&query={lat or 29.760427},{lng or -95.369803}"
    }
    send_discord_notification(test_obj)
    return "已發送休士頓冰藍大蘑菇測試訊息！", 200

@app.route("/trigger_sync", methods=['GET'])
def trigger_sync():
    fetch_and_notify_mushrooms()
    return jsonify({"status": "synced", "count": len(live_mushrooms)}), 200

# ==================== 6. 啟動排程與伺服器 ====================
# 改為每 1 分鐘高頻即時巡檢
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(
    func=fetch_and_notify_mushrooms,
    trigger="interval",
    minutes=1,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=30
)
scheduler.start()

scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="date")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
