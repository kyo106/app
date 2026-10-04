import os
import requests
import time
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ==================== 1. 環境變數與初始化 ====================
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

live_mushrooms = []
notified_ids = set()

LEVEL_MAP = {
    3: "大蘑菇",
    4: "巨大蘑菇"
}

# 經指定配置更新後的蘑菇種類對照表
TYPE_MAP = {
    # 基礎顏色
    "2": "紅色",
    "6": "黃色",
    "3": "灰色",
    "8": "紫色",
    "7": "白色",
    "9": "粉紅色",
    "5": "藍色",
    "10": "神秘活動",
    
    # 元素與特殊蘑菇
    "17": "電",
    "12": "水",
    "11": "火",
    "13": "水晶",
    "18": "毒",
    "ice": "冰藍",
    "event": "神秘活動",
    "mystery": "神秘活動"
}

# 【推播白名單】：只允許元素大菇與特殊蘑菇
TARGET_SPECIAL_TYPES = {"11", "12", "13", "17", "18", "ice", "event", "mystery"}

# ==================== 2. Discord Webhook 推播功能 ====================
def send_discord_notification(mushroom):
    """發送漂亮卡片格式到 Discord 頻道 (含防 429 限流重試)"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL，跳過推播。")
        return

    # 巨大菇用金色，元素大菇用紅色/橘色
    color = 0xF1C40F if mushroom.get('level') == 4 else 0xE74C3C

    embed_data = {
        "title": f"🚨 發現目標蘑菇：{mushroom['title']}",
        "color": color,
        "fields": [
            {"name": "🍄 等級與種類", "value": mushroom['title'], "inline": True},
            {"name": "🌐 座標", "value": f"`{mushroom['lat']}, {mushroom['lng']}`", "inline": True},
            {"name": "🗺️️ Google 地圖導航", "value": f"[點此前往 Google 地圖]({mushroom['gmaps']})", "inline": False}
        ],
        "footer": {
            "text": "皮克敏雷達即時通報 • 無上限自動推送"
        }
    }

    payload = {
        "username": "皮克敏雷達管家",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/616/616490.png",
        "embeds": [embed_data]
    }

    for attempt in range(3):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code in [200, 204]:
                print(f"✅ 已成功推播至 Discord: {mushroom['title']}")
                time.sleep(1)  # 每次發送間隔 1 秒，避免觸發每秒頻率上限
                return
            elif resp.status_code == 429:
                wait_sec = resp.json().get("retry_after", 1.5)
                print(f"⏳ 遭遇頻率限制，等待 {wait_sec} 秒後重試...")
                time.sleep(float(wait_sec) + 0.2)
            else:
                print(f"❌ Discord 推播失敗，狀態碼：{resp.status_code}，原因：{resp.text}")
                break
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")
            break
# ==================== 3. 定期資料擷取與自動通報 ====================
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
        "discovered_within_hours": "6",
        "bbox": "-85.45000,-35.75000,85.45000,61.80000"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mush.odyliao.cc/"
    }

    try:
        res = requests.get(api_url, params=params, headers=headers, timeout=12)
        if res.status_code == 200:
            raw_list = res.json().get("mushrooms", [])
            
            parsed_list = []
            for item in raw_list:
                m_id = str(item.get("id"))
                m_level = item.get("level")
                m_type = str(item.get("type", ""))
                lat = item.get("lat")
                lng = item.get("lng")

                type_name = TYPE_MAP.get(m_type, f"種類{m_type}")
                level_name = LEVEL_MAP.get(m_level, f"等級{m_level}")
                title = f"{type_name} {level_name}"

                m_obj = {
                    "id": m_id,
                    "title": title,
                    "level": m_level,
                    "type": m_type,
                    "lat": lat,
                    "lng": lng,
                    "gmaps": f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
                }
                parsed_list.append(m_obj)

                # 【推播過濾條件】：
                # 1. 巨大蘑菇 (level == 4)
                # 2. 特殊元素大菇 (level == 3 且 type 在白名單中)
                is_giant = (m_level == 4)
                is_target_element = (m_level == 3 and m_type in TARGET_SPECIAL_TYPES)

                if len(notified_ids) > 0 and (is_giant or is_target_element) and m_id not in notified_ids:
                    send_discord_notification(m_obj)

                notified_ids.add(m_id)

            live_mushrooms = parsed_list
            print(f"資料更新完成，共掌握 {len(live_mushrooms)} 朵蘑菇。")
        else:
            print(f"API 回應異常，代碼：{res.status_code}，原因：{res.text}")
    except Exception as e:
        print(f"抓取異常: {e}")

# 每 2 分鐘定期檢查一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", minutes=2)
scheduler.start()

fetch_and_notify_mushrooms()

# ==================== 4. 路由設定 ====================
@app.route("/", methods=['GET'])
def home():
    """提供 UptimeRobot 監控防休眠 (200 OK)"""
    giant_count = sum(1 for m in live_mushrooms if m.get("level") == 4)
    target_count = sum(1 for m in live_mushrooms if m.get("level") == 4 or (m.get("level") == 3 and m.get("type") in TARGET_SPECIAL_TYPES))
    return jsonify({
        "status": "online",
        "message": "Pikmin Bloom Discord Bot is Running!",
        "cached_mushrooms": len(live_mushrooms),
        "target_mushrooms": target_count,
        "giant_mushrooms": giant_count
    }), 200

@app.route("/test_discord", methods=['GET'])
def test_discord():
    """手動測試 Discord Webhook 連線"""
    test_obj = {
        "title": "測試火元素大蘑菇",
        "level": 3,
        "lat": "25.0330",
        "lng": "121.5654",
        "gmaps": "https://www.google.com/maps/search/?api=1&query=25.0330,121.5654"
    }
    send_discord_notification(test_obj)
    return "已發送 Discord 測試訊息，請檢查頻道！", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
