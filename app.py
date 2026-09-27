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
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "0f6c7d5c9921290c9dd87cdb83de9784")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "Bppdi4+cXtgaEKyrEQMO5Tc2MwK+NxZiFNqVWupQPiGT2MTxfuBg5Ij9B0rFMaPr5CFuabOrj+x6T5BVVkyDU1kPxyflwRG7DNplH6Fv7cBgEb4mR5QRLjc/FYSnlgZHbh0Fs1fiG/UGKFIHgKN2ZgdB04t89/1O/w1cDnyilFU=")
# 若要在群組主動廣播，需填入目標 Group ID 或個人 User ID
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID", "")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# 儲存全自動抓回來的巨大/元素蘑菇
live_mushrooms = []
seen_mushroom_ids = set()

# ==================== 1. 全自動後台掃描排程 ====================
def auto_fetch_radar_data():
    """
    此排程每隔數分鐘執行一次，自動抓取目標地圖的資料
    """
    global live_mushrooms, seen_mushroom_ids
    print("雷達開始自動掃描目標區域...")
    
    # 範例：目標雷達 API（可置換為任何提供即時蘑菇 JSON 的端點）
    # 例如：https://pipimushroom.com/ 或其他社群 API
    target_api_url = "https://example.com/api/get_mushrooms"
    
    try:
        # 發送 GET 請求取得目標區域蘑菇資料
        headers = {"User-Agent": "Mozilla/5.0"}
        # response = requests.get(target_api_url, headers=headers, timeout=10)
        # raw_data = response.json()
        
        # --- 模擬抓取到的即時原始資料格式 ---
        raw_data = [
            {"id": "JP-101", "name": "東京鐵塔前", "type": "巨大火蘑菇", "lat": 35.658581, "lng": 139.745438},
            {"id": "TW-202", "name": "台北101旁", "type": "巨大水晶蘑菇", "lat": 25.0339, "lng": 121.5644},
            {"id": "TW-203", "name": "某普通小公園", "type": "普通紅蘑菇", "lat": 25.0400, "lng": 121.5100}
        ]

        # 過濾目標關鍵字
        TARGET_KEYWORDS = ["巨大", "火", "水", "水晶", "電", "毒", "神秘"]
        
        new_found_list = []
        for item in raw_data:
            m_type = item.get("type", "")
            m_id = item.get("id")
            
            # 判斷是否為巨大/元素特殊菇
            if any(k in m_type for k in TARGET_KEYWORDS):
                new_found_list.append(item)
                
                # 若為首次發現且設定了廣播目標，直接發送主動推播
                if m_id not in seen_mushroom_ids:
                    seen_mushroom_ids.add(m_id)
                    broadcast_new_mushroom(item)

        # 更新快取庫
        live_mushrooms = new_found_list
        print(f"掃描完畢，目前掌握 {len(live_mushrooms)} 朵特殊/巨大蘑菇！")

    except Exception as e:
        print(f"自動掃描錯誤: {e}")

def broadcast_new_mushroom(m):
    """主動推播新發現的巨大蘑菇到 LINE 群組"""
    if not TARGET_CHAT_ID:
        return
    
    gmaps_url = f"https://www.google.com/maps/search/?api=1&query={m['lat']},{m['lng']}"
    msg_text = f"🚨 【雷達自動捕獲：{m['type']}】\n📍 地標：{m['name']}\n🌐 座標：{m['lat']}, {m['lng']}\n🗺️ 導航：{gmaps_url}"
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=TARGET_CHAT_ID,
                messages=[TextMessage(text=msg_text)]
            )
        )

# 啟動背景排程：每 5 分鐘自動掃描一次
scheduler = BackgroundScheduler()
scheduler.add_job(func=auto_fetch_radar_data, trigger="interval", minutes=5)
scheduler.start()

# ==================== 2. LINE 查詢與互動處理 ====================
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

    # 指令：查巨大 / 雷達
    if user_text in ["查巨大", "巨大雷達", "雷達"]:
        if not live_mushrooms:
            reply = "目前雷達掃描區域內暫無巨大特殊蘑菇！"
        else:
            lines = []
            for m in live_mushrooms[:5]:  # 只取前 5 筆避免洗版
                lines.append(f"🍄 {m['type']} - {m['name']}\n`{m['lat']}, {m['lng']}`")
            reply = "📡 【全球雷達即時自動偵測】\n\n" + "\n\n".join(lines)
        
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
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
