import os
import requests
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# --- LINE 設定讀取 ---
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "0f6c7d5c9921290c9dd87cdb83de9784")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "Bppdi4+cXtgaEKyrEQMO5Tc2MwK+NxZiFNqVWupQPiGT2MTxfuBg5Ij9B0rFMaPr5CFuabOrj+x6T5BVVkyDU1kPxyflwRG7DNplH6Fv7cBgEb4mR5QRLjc/FYSnlgZHbh0Fs1fiG/UGKFIHgKN2ZgdB04t89/1O/w1cDnyilFU=")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# 儲存全自動抓取的最新蘑菇清單
live_mushrooms = []

# 等級與類型對應表
LEVEL_MAP = {3: "大蘑菇", 4: "巨大蘑菇"}
TYPE_MAP = {
    "1": "紅", "2": "黃", "3": "藍", "4": "白", "5": "紫",
    "6": "黑", "7": "粉", "8": "水", "9": "火", "11": "水晶",
    "12": "毒", "13": "電", "17": "發光", "18": "活動神秘", "ice": "冰"
}

# ==================== 1. 自動向 mush.odyliao.cc 抓取資料 ====================
def fetch_mushrooms_from_odyliao():
    """從 mush.odyliao.cc 全自動抓取最新巨大與元素蘑菇"""
    global live_mushrooms
    print("開始從 mush.odyliao.cc 抓取最新蘑菇情報...")
    
    url = "https://mush.odyliao.cc/api/mushrooms"
    params = {
        "limit": "1000",
        "cache": "brief",
        "levels": "3,4",
        "types": "11,12,13,17,18,2,3,5,6,7,8,9,ice",
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
        response = requests.get(url, params=params, headers=headers, timeout=12)
        if response.status_code == 200:
            data = response.json()
            raw_list = data.get("mushrooms", [])
            print(f"成功取得資料！總筆數：{len(raw_list)}")

            parsed_list = []
            for item in raw_list:
                m_level = item.get("level")
                m_type = str(item.get("type", ""))
                lat = item.get("lat")
                lng = item.get("lng")
                
                type_name = TYPE_MAP.get(m_type, f"類型{m_type}")
                level_name = LEVEL_MAP.get(m_level, f"等級{m_level}")
                title = f"{type_name} {level_name}"

                parsed_list.append({
                    "title": title,
                    "level": m_level,
                    "lat": lat,
                    "lng": lng,
                    "gmaps": f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
                })

            live_mushrooms = parsed_list
            print(f"快取更新完成！目前鎖定 {len(live_mushrooms)} 朵特殊蘑菇。")
        else:
            print(f"請求失敗，狀態碼：{response.status_code}")
    except Exception as e:
        print(f"抓取發生錯誤：{e}")

# 每 3 分鐘自動更新一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_mushrooms_from_odyliao, trigger="interval", minutes=3)
scheduler.start()

# 程式啟動時立刻執行一次抓取
fetch_mushrooms_from_odyliao()

# ==================== 2. LINE Webhook 伺服器 ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_text = event.message.text.strip()

    if user_text in ["查巨大", "巨大", "雷達"]:
        if not live_mushrooms:
            reply = "目前暫無最新蘑菇情報，請稍候再試！"
        else:
            # 優先顯示 level=4（巨大蘑菇），如果沒有則顯示大蘑菇
            giants = [m for m in live_mushrooms if m.get("level") == 4]
            display_items = (giants if giants else live_mushrooms)[:5]
            
            lines = []
            for idx, m in enumerate(display_items, start=1):
                lines.append(
                    f"{idx}. 🍄 {m['title']}\n"
                    f"   🌐 座標：`{m['lat']}, {m['lng']}`\n"
                    f"   🗺️ 導航：{m['gmaps']}"
                )
            
            header = "📡 【最新巨大蘑菇情報】\n" if giants else "📡 【最新大元素蘑菇情報】\n"
            reply = header + "\n\n".join(lines)
            
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
