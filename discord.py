import os
import re
import threading
import asyncio
import discord
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# --- 讀取環境變數 ---
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "0f6c7d5c9921290c9dd87cdb83de9784")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "Bppdi4+cXtgaEKyrEQMO5Tc2MwK+NxZiFNqVWupQPiGT2MTxfuBg5Ij9B0rFMaPr5CFuabOrj+x6T5BVVkyDU1kPxyflwRG7DNplH6Fv7cBgEb4mR5QRLjc/FYSnlgZHbh0Fs1fiG/UGKFIHgKN2ZgdB04t89/1O/w1cDnyilFU=")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
raw_channel_id = os.environ.get("DISCORD_CHANNEL_ID", "0")
TARGET_CHANNEL_ID = int(raw_channel_id) if raw_channel_id.isdigit() else 0

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# 儲存監聽到的即時蘑菇情報
live_mushrooms = []

# ==================== Discord 監聽客戶端 ====================
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)

@discord_client.event
async def on_ready():
    print(f"✅ Discord 監聽已成功啟動！登入身分：{discord_client.user}")

@discord_client.event
async def on_message(message):
    global live_mushrooms
    # 忽略自己的發言
    if message.author == discord_client.user:
        return
    # 若有指定頻道，只監聽目標頻道
    if TARGET_CHANNEL_ID and message.channel.id != TARGET_CHANNEL_ID:
        return

    content = message.content
    print(f"收到 Discord 訊息: {content}")
    
    TARGET_KEYWORDS = ["巨大", "火", "水", "水晶", "電", "毒", "神秘", "活動", "蘑菇", "菇"]
    
    # 判斷是否包含關鍵字
    if any(k in content for k in TARGET_KEYWORDS):
        # 嘗試擷取經緯度座標 (支援常見格式)
        coord_match = re.search(r"(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)", content)
        lat, lng = (coord_match.group(1), coord_match.group(2)) if coord_match else ("", "")

        mushroom_info = {
            "title": content.split("\n")[0][:40],
            "raw": content,
            "lat": lat,
            "lng": lng
        }
        
        # 存入清單，保留最新 30 筆
        live_mushrooms.insert(0, mushroom_info)
        if len(live_mushrooms) > 30:
            live_mushrooms.pop()
            
        print(f"⭐ 成功捕獲蘑菇情報，目前庫存: {len(live_mushrooms)} 筆")

# ==================== 啟動 Discord 背景連線 ====================
def start_discord_bot():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(discord_client.start(DISCORD_TOKEN))
    except Exception as e:
        print(f"❌ Discord 連線失敗: {e}")

if DISCORD_TOKEN:
    print("正在啟動 Discord 監聽背景執行緒...")
    t = threading.Thread(target=start_discord_bot, daemon=True)
    t.start()
else:
    print("⚠️ 警告：未設定 DISCORD_TOKEN 環境變數，Discord 監聽不會啟動！")

# ==================== LINE 伺服器路由 ====================
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

    if user_text in ["雷達", "查巨大", "巨大"]:
        if not live_mushrooms:
            reply = "目前暫無監聽到任何巨大特殊蘑菇情報！"
        else:
            lines = []
            for idx, m in enumerate(live_mushrooms[:5], start=1):
                loc = f"\n  座標：{m['lat']},{m['lng']}" if m['lat'] else ""
                lines.append(f"{idx}. {m['title']}{loc}")
            reply = "📡 【Discord 即時監聽情報清單】\n\n" + "\n\n".join(lines)
            
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
