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
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, JoinEvent

app = Flask(__name__)

# --- 1. 讀取環境變數與初始化 handler (必須放在最前面) ---
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "0f6c7d5c9921290c9dd87cdb83de9784")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "Bppdi4+cXtgaEKyrEQMO5Tc2MwK+NxZiFNqVWupQPiGT2MTxfuBg5Ij9B0rFMaPr5CFuabOrj+x6T5BVVkyDU1kPxyflwRG7DNplH6Fv7cBgEb4mR5QRLjc/FYSnlgZHbh0Fs1fiG/UGKFIHgKN2ZgdB04t89/1O/w1cDnyilFU=")
TARGET_LINE_ID = os.environ.get("TARGET_LINE_ID", "")

# 關鍵：先定義好 handler 與 configuration
handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# 儲存資料庫與已推播的 ID
live_mushrooms = []
notified_ids = set()

LEVEL_MAP = {3: "大蘑菇", 4: "巨大蘑菇"}
TYPE_MAP = {
    "1": "紅", "2": "黃", "3": "藍", "4": "白", "5": "紫",
    "6": "黑", "7": "粉", "8": "水", "9": "火", "11": "水晶",
    "12": "毒", "13": "電", "17": "發光", "18": "活動神秘", "ice": "冰"
}

# ==================== 2. 主動推播與資料抓取 ====================
def send_line_push_notification(mushroom):
    """主動發送 LINE 訊息通知"""
    if not TARGET_LINE_ID:
        return

    msg = (
        f"🚨 【發現全新巨大蘑菇！】\n"
        f"🍄 種類：{mushroom['title']}\n"
        f"🌐 座標：`{mushroom['lat']}, {mushroom['lng']}`\n"
        f"🗺️ Google 地圖導航：\n{mushroom['gmaps']}"
    )

    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=TARGET_LINE_ID,
                    messages=[TextMessage(text=msg)]
                )
            )
        print(f"✅ 已成功推播蘑菇至 LINE: {mushroom['title']}")
    except Exception as e:
        print(f"❌ 推播訊息失敗: {e}")

def fetch_and_notify_mushrooms():
    """定期抓取資料，若有新出現的巨大菇則觸發主動推播"""
    global live_mushrooms, notified_ids
    print("📡 開始同步 mush.odyliao.cc 點位...")

    api_url = "https://mush.odyliao.cc/api/mushrooms"
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

                type_name = TYPE_MAP.get(m_type, f"類型{m_type}")
                level_name = LEVEL_MAP.get(m_level, f"等級{m_level}")
                title = f"{type_name} {level_name}"

                m_obj = {
                    "id": m_id,
                    "title": title,
                    "level": m_level,
                    "lat": lat,
                    "lng": lng,
                    "gmaps": f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
                }
                parsed_list.append(m_obj)

                # 只針對「全新出現」且為「巨大蘑菇 (level=4)」主動推播
                if len(notified_ids) > 0 and m_level == 4 and m_id not in notified_ids:
                    send_line_push_notification(m_obj)

                notified_ids.add(m_id)

            live_mushrooms = parsed_list
            print(f"更新完成，目前掌握 {len(live_mushrooms)} 朵蘑菇。")
        else:
            print(f"API 回應異常，代碼：{res.status_code}")
    except Exception as e:
        print(f"更新點位時發生錯誤: {e}")

# 排程：每 2 分鐘檢查一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", minutes=2)
scheduler.start()

fetch_and_notify_mushrooms()

# ==================== 3. LINE Webhook 伺服器與事件處理 ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# (1) 機器人加入群組事件：自動印出群組 ID
@handler.add(JoinEvent)
def handle_join(event):
    if event.source.type == "group":
        group_id = event.source.group_id
        print(f"🎉 機器人已加入群組，群組 ID 為: {group_id}")
        reply_text = f"大家好！皮克敏雷達已就緒。\n本群組 ID 為：\n{group_id}\n\n請將此數值填入 Render 的 TARGET_LINE_ID 環境變數中！"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )

# (2) 接收使用者文字訊息
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_text = event.message.text.strip()
    
    # 支援在群組手動發送「查ID」
    if user_text == "查ID":
        source_id = event.source.user_id
        if hasattr(event.source, "group_id") and event.source.group_id:
            source_id = event.source.group_id
        reply = f"你的推播 ID 為：\n{source_id}\n\n請將此數值填入 Render 的 TARGET_LINE_ID 環境變數中！"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    if user_text in ["查巨大", "巨大", "雷達"]:
        giants = [m for m in live_mushrooms if m.get("level") == 4]
        items = (giants if giants else live_mushrooms)[:5]
        
        if not items:
            reply = "目前暫無符合條件的蘑菇！"
        else:
            lines = [f"{i}. 🍄 {m['title']}\n   🌐 座標：`{m['lat']}, {m['lng']}`\n   🗺️ 導航：{m['gmaps']}" for i, m in enumerate(items, 1)]
            reply = "📡 【最新巨大蘑菇清單】\n\n" + "\n\n".join(lines)
            
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
