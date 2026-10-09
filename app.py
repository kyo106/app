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

# ==================== 2. 地理位置解析函式 ====================
def get_location_name(item, lat, lng):
    """全面搜尋 API 回傳的所有可能地區欄位，或進行座標反查"""
    potential_keys = [
        "region", "area", "location", "place", "city", 
        "country", "address", "poi", "name", "note", "desc"
    ]
    
    # 1. 檢查第一層欄位
    for key in potential_keys:
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # 2. 檢查巢狀物件 (例如 item["geo"] 或 item["data"])
    for nested_key in ["geo", "data", "meta", "info"]:
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            for key in potential_keys:
                val = nested.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()

    # 3. 組合國家與城市
    country = item.get("country", "")
    city = item.get("city", "")
    if country or city:
        return f"{country} {city}".strip()

    # 4. 若有經緯度，嘗試透過 OpenStreetMap 進行免費地理反查
    if lat and lng and str(lat).strip() not in ["", "None"] and str(lng).strip() not in ["", "None"]:
        try:
            geo_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lng}&format=json&accept-language=zh-TW"
            headers = {"User-Agent": "PikminRadarBot/1.0"}
            r = requests.get(geo_url, headers=headers, timeout=3)
            if r.status_code == 200:
                addr = r.json().get("address", {})
                country_name = addr.get("country", "")
                city_name = addr.get("city") or addr.get("state") or addr.get("town") or addr.get("county") or ""
                if country_name or city_name:
                    return f"{country_name} - {city_name}".strip()
        except Exception:
            pass

    return "站方未提供具體地名 (GPS已鎖定)"

# ==================== 3. Discord Webhook 發送函式 ====================
def send_discord_notification(mushroom):
    """發送卡片訊息至 Discord (含所在區域、座標與防限流機制)"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL，跳過推播。")
        return

    # 計算台灣時間 (UTC+8)
    tz_tw = timezone(timedelta(hours=8))
    now_tw = datetime.now(tz_tw)
    time_str = now_tw.strftime("%Y-%m-%d %H:%M:%S")

    color = 0xF1C40F if mushroom.get('level') == 4 else 0x3498DB

    # 座標文字排版
    if mushroom['has_coords']:
        coord_text = f"`{mushroom['lat']}, {mushroom['lng']}`"
        gmaps_text = f"[點此前往 Google 地圖]({mushroom['gmaps']})"
    else:
        coord_text = "🔒 站方已隱藏/鎖定 GPS"
        gmaps_text = f"[點此前往雷達網站檢視]({mushroom['gmaps']})"

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
                time.sleep(1)  # 推播間隔 1 秒，防 Discord 限流
                return
            elif resp.status_code == 429:
                wait_sec = resp.json().get("retry_after", 1.5)
                print(f"⏳ 遭遇 Discord 限流，等待 {wait_sec} 秒...")
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
            
            # 方便日誌檢查原站目前使用的欄位結構
            if len(raw_list) > 0:
                print(f"🔍 檢視第一筆資料結構範例: {raw_list[0]}")

            parsed_list = []
            for item in raw_list:
                m_id = str(item.get("id"))
                m_level = item.get("level")
                m_type = str(item.get("type", ""))
                
                # 相容多種經緯度命名
                lat = item.get("lat") or item.get("latitude")
                lng = item.get("lng") or item.get("longitude")
                
                has_coords = (lat is not None and lng is not None and str(lat).strip() not in ["", "None"] and str(lng).strip() not in ["", "None"])

                location_name = get_location_name(item, lat, lng)
                type_name = TYPE_MAP.get(m_type, f"種類{m_type}")
                level_name = LEVEL_MAP.get(m_level, f"等級{m_level}")
                title = f"{type_name} {level_name}"

                lat_str = str(lat) if has_coords else "未提供"
                lng_str = str(lng) if has_coords else "未提供"
                gmaps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}" if has_coords else "https://mush.odyliao.cc/"

                m_obj = {
                    "id": m_id,
                    "title": title,
                    "level": m_level,
                    "type": m_type,
                    "location": location_name,
                    "lat": lat_str,
                    "lng": lng_str,
                    "has_coords": has_coords,
                    "gmaps": gmaps_url
                }
                parsed_list.append(m_obj)

                # 【推播過濾】：
                # 排除神秘活動與活動菇 (10, 19, event, mystery)
                # 僅通報：非活動的巨大菇 或 純元素大菇 (電、水、火、水晶、毒、冰藍)
                is_excluded = (m_type in ["10", "19", "event", "mystery"])
                is_target = not is_excluded and (m_level == 4 or (m_level == 3 and m_type in TARGET_SPECIAL_TYPES))

                # 初次啟動只建立 baseline，後續出現新點位才推播
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
    """提供 UptimeRobot 監控防休眠 (回傳 200 OK)"""
    return jsonify({
        "status": "online",
        "message": "Pikmin Bloom Discord Bot is Running!",
        "cached_mushrooms": len(live_mushrooms)
    }), 200

@app.route("/test_discord", methods=['GET'])
def test_discord():
    """手動測試 Discord Webhook 連線"""
    test_obj = {
        "title": "測試火元素大蘑菇",
        "level": 3,
        "location": "巴西-瑪瑙斯 Manaus",
        "lat": "-3.119027",
        "lng": "-60.021731",
        "has_coords": True,
        "gmaps": "https://www.google.com/maps/search/?api=1&query=-3.119027,-60.021731"
    }
    send_discord_notification(test_obj)
    return "已發送 Discord 測試訊息，請檢查頻道！", 200

# ==================== 6. 啟動排程與伺服器 ====================
# 背景排程設定：每 2 分鐘檢查一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", minutes=2)
scheduler.start()

# 延遲背景任務，不阻塞主執行緒開 Port
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="date")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
