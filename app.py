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

# ==================== 1. 環境變數與初始化 ====================
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

# 經多輪實機比對校正後的蘑菇種類對照表
TYPE_MAP = {
    # 基礎顏色
    "2": "紅",       # 校正：2 實為紅色大菇
    "6": "黃",       # 校正：6 實為黃色大菇
    "3": "灰色",     # 3 實為灰色(岩)
    "5": "紫色",     # 5 實為紫色
    "7": "白色",     # 7 實為白色
    "12": "紫色",    # 12 實為紫色
    "13": "粉紅",    # 13 實為粉紅
    
    # 預留未完全確定的代碼 (若遇到將直接顯示種類與編號)
    "1": "種類1",
    "4": "藍",       # 待確認是否為藍色
    
    # 元素與特殊蘑菇
    "8": "電",
    "9": "火",
    "11": "水晶",
    "17": "電",
    "18": "毒",
    "26": "冰藍",    # 26 實為冰藍
    "ice": "冰藍",
    "mystery": "神秘活動"
}

# ==================== 2. 主動推播功能 ====================
def send_line_push_notification(mushroom):
    """主動發送 LINE 訊息通知"""
    if not TARGET_LINE_ID:
        return

    msg = (
        f"🚨 【發現全新蘑菇點位！】\n"
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

# ==================== 3. 定期資料擷取與自動通報 ====================
def fetch_and_notify_mushrooms():
    global live_mushrooms, notified_ids
    print("📡 開始同步 mush.odyliao.cc 點位...")

    api_url = "https://mush.odyliao.cc/api/mushrooms"
    params = {
        "limit": "1000",
        "cache": "brief",
        "levels": "3,4",
        "types": "1,2,3,4,5,6,7,8,9,11,12,13,17,18,26,ice",
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

                # 初次啟動記錄 baseline；之後只要出現新蘑菇 (大菇 3 或 巨大 4) 立即推播
                if len(notified_ids) > 0 and m_level in [3, 4] and m_id not in notified_ids:
                    send_line_push_notification(m_obj)

                notified_ids.add(m_id)

            live_mushrooms = parsed_list
            print(f"資料更新完成，共掌握 {len(live_mushrooms)} 朵蘑菇。")
        else:
            print(f"API 回應異常，代碼：{res.status_code}")
    except Exception as e:
        print(f"抓取異常: {e}")

# 每 2 分鐘定期檢查一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_notify_mushrooms, trigger="interval", minutes=2)
scheduler.start()

fetch_and_notify_mushrooms()

# ==================== 4. 首頁與 LINE Webhook 路由 ====================
@app.route("/", methods=['GET'])
def home():
    """提供 UptimeRobot 監控專用的健康檢查端點 (回傳 200 OK)"""
    return "Pikmin Bloom Bot is Running!", 200

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
        reply_text = f"大家好！皮克敏雷達已加入群組。\n本群組 ID 為：\n{group_id}\n\n請將此數值填入 Render 的 TARGET_LINE_ID 環境變數。"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
            )

# ==================== 5. 完整 LINE 訊息指令處理 ====================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_text = event.message.text.strip()

    # 指令 1：查詢聊天室/群組 ID
    if user_text == "查ID":
        source_id = event.source.user_id
        if hasattr(event.source, "group_id") and event.source.group_id:
            source_id = event.source.group_id
        reply = f"📌 當前聊天室推播 ID：\n{source_id}\n\n若要接收自動推播，請將此 ID 填入 Render 的 TARGET_LINE_ID。"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    # 指令 2：測試推播連線
    if user_text == "測試推播":
        if not TARGET_LINE_ID:
            reply = "⚠️ 尚未設定 TARGET_LINE_ID，無法執行推播！"
        elif not live_mushrooms:
            reply = "⚠️ 目前記憶體內尚未載入點位資料，請稍候重試！"
        else:
            send_line_push_notification(live_mushrooms[0])
            reply = "🚀 已嘗試送出推播測試！請檢查是否有收到推播訊息。"
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    # 指令 3：查詢系統狀態
    if user_text == "狀態":
        giant_count = sum(1 for m in live_mushrooms if m.get("level") == 4)
        reply = (
            f"🤖 【皮克敏雷達運行狀態】\n"
            f"✅ 背景排程：每 2 分鐘同步一次\n"
            f"📊 目前快取總菇數：{len(live_mushrooms)} 筆\n"
            f"🌟 巨大蘑菇數量：{giant_count} 筆\n"
            f"🎯 推播目標 ID：{TARGET_LINE_ID[:8]}... (已就緒)" if TARGET_LINE_ID else "⚠️ 未設定 TARGET_LINE_ID"
        )
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    # 指令 4：專門查詢巨大蘑菇 (Level 4)
    if user_text in ["查巨大", "巨大"]:
        giants = [m for m in live_mushrooms if m.get("level") == 4]
        if not giants:
            reply = "目前全球雷達暫無發現 Level 4 巨大蘑菇！\n（可改傳「雷達」查看最新特殊大菇）"
        else:
            lines = [f"{i}. 🍄 {m['title']}\n   🌐 座標：`{m['lat']}, {m['lng']}`\n   🗺️ 導航：{m['gmaps']}" for i, m in enumerate(giants[:5], 1)]
            reply = "📡 【最新巨大蘑菇清單】\n\n" + "\n\n".join(lines)
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

    # 指令 5：綜合雷達清單 (優先巨大，不足補大菇)
    if user_text in ["雷達", "查大菇"]:
        if not live_mushrooms:
            reply = "目前尚未取得點位資料，請稍候重試！"
        else:
            items = live_mushrooms[:5]
            lines = [f"{i}. 🍄 {m['title']}\n   🌐 座標：`{m['lat']}, {m['lng']}`\n   🗺️ 導航：{m['gmaps']}" for i, m in enumerate(items, 1)]
            reply = "📡 【最新特殊蘑菇雷達點位】\n\n" + "\n\n".join(lines)
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
