import os
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

# 地名座標快取，避免重複呼叫地理編碼
LOCATION_COORD_CACHE = {}

LEVEL_MAP = {
    3: "大蘑菇",
    4: "巨大蘑菇"
}

TYPE_MAP = {
    # 基礎顏色 (不推播)
    "2": "紅色",
    "6": "黃色",
    "3": "灰色",
    "8": "紫色",
    "7": "白色",
    "9": "粉紅色",
    "5": "藍色",
    "10": "神秘活動",
    "19": "活動特殊",
    
    # 元素與特殊蘑菇
    "17": "電",
    "12": "水",
    "11": "火",
    "13": "水晶",
    "18": "毒",
    "ice": "冰藍",
    "26": "冰藍",
    "event": "神秘活動",
    "mystery": "神秘活動"
}

# 【推播白名單】：只允許純元素蘑菇 (排除 10, 19, event, mystery)
TARGET_SPECIAL_TYPES = {"11", "12", "13", "17", "18", "26", "ice"}

# ==================== 2. 地理位置解析與概略座標計算 ====================
def extract_location_text(item):
    """精準對接原站 location_country 與 location_city"""
    country = str(item.get("location_country") or "").strip()
    city = str(item.get("location_city") or "").strip()

    if country and city:
        return f"{country}-{city}"
    elif country:
        return country
    elif city:
        return city

    # 備用容錯檢查
    for key in ["location", "place", "area", "address"]:
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    return "未知區域"

def geocode_location_to_coords(location_text):
    """根據地名 (如: 巴拿馬-戴維 David) 透過 OSM 計算中心經緯度"""
    if not location_text or location_text == "未知區域":
        return None, None

    if location_text in LOCATION_COORD_CACHE:
        return LOCATION_COORD_CACHE[location_text]

    query_str = location_text.replace("-", " ").strip()
    
    try:
        geo_url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": query_str,
            "format": "json",
            "limit": 1
        }
        headers = {"User-Agent": "PikminRadarBotGeo/1.1 (contact: bot@pikmin.local)"}
        res = requests.get(geo_url, params=params, headers=headers, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                approx_lat = round(float(data[0]["lat"]), 6)
                approx_lng = round(float(data[0]["lon"]), 6)
                LOCATION_COORD_CACHE[location_text] = (approx_lat, approx_lng)
                return approx_lat, approx_lng
    except Exception as e:
        print(f"地理反查失敗 ({location_text}): {e}")

    LOCATION_COORD_CACHE[location_text] = (None, None)
    return None, None

# ==================== 3. Discord Webhook 發送函式 ====================
def send_discord_notification(mushroom):
    """發送卡片訊息至 Discord (含所在區域、概略座標與 Google 地圖)"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL，跳過推播。")
        return

    # 計算台灣時間 (UTC+8)
    tz_tw = timezone(timedelta(hours=8))
    now_tw = datetime.now(tz_tw)
    time_str = now_tw.strftime("%Y-%m-%d %H:%M:%S")

    color = 0xF1C40F if mushroom.get('level') == 4 else 0x3498DB

    if mushroom.get('is_exact_gps'):
        coord_text = f"`{mushroom['lat']}, {mushroom['lng']}` (精準座標)"
        gmaps_text = f"[點此前向 Google 地圖]({mushroom['gmaps']})"
    elif mushroom.get('lat') and mushroom.get('lat') != "未提供":
        coord_text = f"{mushroom['lat']}, {mushroom['lng']}"
        gmaps_text = f"[點此導航至該區域中心]({mushroom['gmaps']})"
    else:
        coord_text = "🔒 原站已隱藏 GPS (無法估算城鎮中心)"
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
                print(f"⏳ 遭遇限流，等待 {wait_sec} 秒...")
                time.sleep(float(wait_sec) + 0.2)
            else:
                print(f"❌ Discord 推播失敗，狀態碼：{resp.status_code}")
                break
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")
            break

# ==================== 4. 點位同步與通報任務 ====================
def fetch_and_notify_mushrooms():
    global live_mushrooms, notified_ids
    print("📡 開始同步 mush.odyliao.cc 點位...")

    api_url = "https://mush.odyliao.cc/api/mushrooms"
    params = {
        "limit": "1000",
        "cache": "brief",
        "levels": "3,4",
        "sort": "discovered-desc",
        "prioritize_low": "1",
        "under_five": "1",
        "bbox": "-85.45000,-35.75000,85.45000,61.80000"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mush.odyliao.cc/"
    }

    try:
        res = requests.get(api_url, params=params, headers=headers, timeout=10)
        if res.status_code == 200:
            raw_list = res.json().get("mushrooms", [])
            parsed_list = []

            for item in raw_list:
                m_id = str(item.get("id"))
                m_level = item.get("level")
                m_type = str(item.get("type", ""))

                # 精確讀取 location_country 與 location_city
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
                    # 原站 GPS 為 null，透過地名計算概略座標
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
                # 排除神秘活動與活動菇 (10, 19, event, mystery)
                # 僅通報：非活動巨大菇 或 純元素大菇 (電、水、火、水晶、毒、冰藍)
                is_excluded = (m_type in ["10", "19", "event", "mystery"])
                is_target = not is_excluded and (m_level == 4 or (m_level == 3 and m_type in TARGET_SPECIAL_TYPES))

                if len(notified_ids) > 0 and is_target and m_id not in notified_ids:
                    send_discord_notification(m_obj)

                notified_ids.add(m_id)

            live_mushrooms = parsed_list
            print(f"資料更新完成，共掌握 {len(live_mushrooms)} 朵蘑菇。")
        else:
            print(f"API 回應異常，代碼：{res.status_code}，原因：{res.text}")
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
    """手動測試巴拿馬-戴維推播卡片"""
    loc_test = "巴拿馬-戴維 David"
    lat, lng = geocode_location_to_coords(loc_test)
    test_obj = {
        "title": "毒 大蘑菇",
        "level": 3,
        "location": loc_test,
        "lat": str(lat) if lat else "8.4273",
        "lng": str(lng) if lng else "-82.4309",
        "is_exact_gps": False,
        "gmaps": f"https://www.google.com/maps/search/?api=1&query={lat or 8.4273},{lng or -82.4309}"
    }
    send_discord_notification(test_obj)
    return "已發送巴拿馬-戴維測試訊息，請至 Discord 查看！", 200

@app.route("/trigger_sync", methods=['GET'])
def trigger_sync():
    """強制手動執行一次點位抓取"""
    fetch_and_notify_mushrooms()
    return jsonify({"status": "synced", "count": len(live_mushrooms)}), 200

# ==================== 6. 啟動排程與伺服器 ====================
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", minutes=2)
scheduler.start()

scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="date")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
