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

# ==================== 1. 基礎設定與環境變數 ====================
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
TARGET_LINE_ID = os.environ.get("TARGET_LINE_ID", "")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

live_mushrooms = []
notified_ids = set()

LEVEL_MAP = {
    3: "大蘑菇",
    4: "巨大蘑菇"
}

# 經實機校正後的蘑菇種類對照表
TYPE_MAP = {
    # 基礎 7 色
    "1": "紅",
    "2": "黃",
    "3": "藍",
    "4": "白",
    "5": "紫",
    "6": "灰色",
    "7": "粉紅",
    # 特殊元素
    "8": "一般水",
    "9": "一般火",
    "11": "水晶",
    "12": "水",
    "13": "火",
    "17": "電",
    "18": "毒",
    "26": "冰藍",
    "ice": "冰藍",
    "mystery": "神秘活動"
}

# ==================== 2. 主動推播發送 ====================
def send_line_push_notification(mushroom):
    """偵測到新巨大菇時主動發送 LINE 推播"""
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
        print(f"✅ 已成功推播至 LINE: {mushroom['title']}")
    except Exception as e:
        print(f"❌ 推播失敗: {e}")

# ==================== 3. 定期向 API 同步最新資料 ====================
def fetch_and_notify_mushrooms():
    global live_mushrooms, notified_ids
    print("📡 開始同步 mush.odyliao.cc 點位...")

    api_url = "https://mush.odyliao.cc/api/mushrooms"
    # 鎖定大菇(3)、巨大菇(4)，包含各類元素與普通顏色
    params = {
        "limit": "1000",
        "cache": "brief",
        "levels": "3,4",
        "types": "1,2,3,4,5,6,7,11,12,13,17,18,26,ice",
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
                    "lat": lat,
                    "lng": lng,
                    "gmaps": f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
                }
                parsed_list.append(m_obj)

                # 排除初次啟動狂洗：僅針對之後發現且未通知過的「巨大蘑菇 (level=4)」推播
                if len(notified_ids) > 0 and m_level == 4 and m_id not in notified_ids:
                    send_line_push_notification(m_obj)

                notified_ids.add(m_id)

            live_mushrooms = parsed_list
            print(f"資料更新完成，共掌握 {len(live_mushrooms)} 朵蘑菇。")
        else:
            print(f"API 回應異常，代碼：{res.status_code}")
    except Exception as e:
        print(f"抓取異常: {e}")

# 排程：每 2 分鐘自動更新一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", minutes=2)
scheduler.start()

fetch_and_notify_mushrooms()

# ==================== 4. LINE Webhook 伺服器 ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(JoinEvent)
def handle_join(event):
    """機器人加入群組時回傳群組 ID"""
    if event.source.type == "group":
        group_id = event.source.group_id
        reply_text = f"大家好！皮克敏雷達已就緒。\n本群組 ID 為：\n{group_id}\n\n請將此 ID 填入 Render 的 TARGET_LINE_ID 環境變數。"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
            )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_text = event.message.text.strip()

    # 查 ID
    if user_text == "查ID":
        source_id = event.source.user_id
        if hasattr(event.source, "group_id") and event.source.group_id:
            source_id = event.source.group_id
        reply = f"你的推播 ID 為：\n{source_id}"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    # 測試推播
    if user_text == "測試推播":
        if not TARGET_LINE_ID:
            reply = "尚未設定 TARGET_LINE_ID，無法推播！"
        elif not live_mushrooms:
            reply = "目前尚未抓取到蘑菇資料，請稍候重試！"
        else:
            send_line_push_notification(live_mushrooms[0])
            reply = "已送出推播測試，請檢查是否收到訊息！"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    # 查巨大 / 雷達
    if user_text in ["查巨大", "巨大", "雷達"]:
        giants = [m for m in live_mushrooms if m.get("level") == 4]
        items = (giants if giants else live_mushrooms)[:5]

        if not items:
            reply = "目前暫無符合條件的蘑菇！"
        else:
            lines = [f"{i}. 🍄 {m['title']}\n   🌐 座標：`{m['lat']}, {m['lng']}`\n   🗺️ 導航：{m['gmaps']}" for i, m in enumerate(items, 1)]
            header = "📡 【最新巨大蘑菇清單】\n\n" if giants else "📡 【最新特殊大菇清單】\n\n"
            reply = header + "\n\n".join(lines)

        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
