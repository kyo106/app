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

# --- 讀取環境變數 ---
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
TARGET_LINE_ID = os.environ.get("TARGET_LINE_ID", "")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# 儲存資料庫與已推播的 ID
live_mushrooms = []
notified_ids = set()
is_first_run = True  # 開機暖機開關，避免重啟瞬間洗版

# 蘑菇中文對照
TYPE_MAP = {
    "8": "大水蘑菇",
    "9": "大火蘑菇",
    "11": "大水晶蘑菇",
    "12": "大毒蘑菇",
    "13": "大電蘑菇"
}
# 限定只推播這五種元素代碼
TARGET_ELEMENT_TYPES = {"8", "9", "11", "12", "13"}

# ==================== 1. 主動推播函式 ====================
def send_line_push_notification(mushroom):
    """主動發送 LINE 訊息通知"""
    if not TARGET_LINE_ID:
        return

    msg = (
        f"⚡ 【發現全新特殊元素菇！】\n"
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
        print(f"✅ 已推播至 LINE: {mushroom['title']}")
    except Exception as e:
        print(f"❌ 推播失敗: {e}")

# ==================== 2. 定期排程檢查更新 ====================
def fetch_and_notify_mushrooms():
    """定期抓取資料，只要發現全新出現的指定元素大菇立即推播"""
    global live_mushrooms, notified_ids, is_first_run
    
    api_url = "https://mush.odyliao.cc/api/mushrooms"
    # levels=3 代表大菇；types 鎖定 8(水), 9(火), 11(水晶), 12(毒), 13(電)
    params = {
        "limit": "1000",
        "cache": "brief",
        "levels": "3",
        "types": "8,9,11,12,13",
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
            new_targets_to_push = []

            for item in raw_list:
                m_id = str(item.get("id"))
                m_level = item.get("level")
                m_type = str(item.get("type", ""))
                lat = item.get("lat")
                lng = item.get("lng")

                # 只處理五大元素
                if m_type in TARGET_ELEMENT_TYPES:
                    title = TYPE_MAP.get(m_type, "特殊大蘑菇")
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

                    # 開機暖機完畢後，只要偵測到新 ID 且為這五種元素大菇，立刻推播
                    if not is_first_run and m_id not in notified_ids and m_level == 3:
                        new_targets_to_push.append(m_obj)

                notified_ids.add(m_id)

            live_mushrooms = parsed_list

            if is_first_run:
                is_first_run = False
                print(f"✅ 初始化完畢！已記錄現有 {len(notified_ids)} 個點位（不洗版）")
            else:
                for new_m in new_targets_to_push:
                    print(f"🚨 捕獲新元素菇，立即推播：{new_m['title']}")
                    send_line_push_notification(new_m)
        else:
            print(f"API 回應異常，代碼：{res.status_code}")
    except Exception as e:
        print(f"抓取異常: {e}")

# 排程：每 45 秒檢查一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", seconds=45)
scheduler.start()

fetch_and_notify_mushrooms()

# ==================== 3. LINE 路由與事件 ====================
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
    if event.source.type == "group":
        group_id = event.source.group_id
        reply_text = f"大家好！雷達已就緒。\n群組 ID 為：\n{group_id}\n\n已記錄供推播使用！"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
            )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_text = event.message.text.strip()

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

    if user_text == "測試推播":
        if not live_mushrooms:
            reply = "目前清單尚無元素蘑菇可供測試！"
        else:
            send_line_push_notification(live_mushrooms[0])
            reply = "已送出元素菇推播測試！請檢查群組。"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    if user_text in ["查蘑菇", "蘑菇", "雷達"]:
        if not live_mushrooms:
            reply = "目前暫無大火/大水/大水晶/大毒/大電蘑菇情報！"
        else:
            lines = [f"{i}. 🍄 {m['title']}\n   🌐 座標：`{m['lat']}, {m['lng']}`\n   🗺️ 導航：{m['gmaps']}" for i, m in enumerate(live_mushrooms[:5], 1)]
            reply = "📡 【最新特殊元素大菇清單】\n\n" + "\n\n".join(lines)
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
