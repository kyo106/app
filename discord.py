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

# --- LINE 設定 ---
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "0f6c7d5c9921290c9dd87cdb83de9784")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "Bppdi4+cXtgaEKyrEQMO5Tc2MwK+NxZiFNqVWupQPiGT2MTxfuBg5Ij9B0rFMaPr5CFuabOrj+x6T5BVVkyDU1kPxyflwRG7DNplH6Fv7cBgEb4mR5QRLjc/FYSnlgZHbh0Fs1fiG/UGKFIHgKN2ZgdB04t89/1O/w1cDnyilFU=")
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID", "")  # 目標群組 ID（可選）

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# --- Discord 設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "你的DISCORD_BOT_TOKEN")
TARGET_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "123456789012345678"))  # 目標頻道 ID

# 快取資料庫
live_mushrooms = []

# ==================== 1. Discord 即時監聽核心 ====================
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)

@discord_client.event
async def on_ready():
    print(f"Discord 監聽已啟動，登入身分：{discord_client.user}")

@discord_client.event
async def on_message(message):
    global live_mushrooms
    # 避免監聽自己，且只限定目標情報頻道
    if message.author == discord_client.user:
        return
    if TARGET_CHANNEL_ID and message.channel.id != TARGET_CHANNEL_ID:
        return

    content = message.content
    TARGET_KEYWORDS = ["巨大", "火", "水", "水晶", "電", "毒", "神秘", "活動"]
    
    # 只要訊息包含關鍵字
    if any(k in content for k in TARGET_KEYWORDS):
        print(f"收到符合條件的情報：\n{content}")
        
        # 使用正則表達式擷取經緯度 (支援常見的 xx.xxxx, yy.yyyy 格式)
        coord_match = re.search(r"(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)", content)
        lat, lng = (coord_match.group(1), coord_match.group(2)) if coord_match else ("", "")

        # 整理蘑菇資料物件
        mushroom_obj = {
            "title": content.split("\n")[0][:40], # 取前40字作標題
            "raw": content,
            "lat": lat,
            "lng": lng
        }
        
        # 存入快取庫（最新資料放最前，最多保留 30 筆）
        live_mushrooms.insert(0, mushroom_obj)
        if len(live_mushrooms) > 30:
            live_mushrooms.pop()

        # 若有設定群組 ID，主動進行 LINE 廣播
        if TARGET_CHAT_ID:
            broadcast_line_message(mushroom_obj)

def broadcast_line_message(m):
    """主動推播至 LINE"""
    nav_text = f"\n🗺️ 導航：https://www.google.com/maps/search/?api=1&query={m['lat']},{m['lng']}" if m['lat'] else ""
    msg_text = f"🚨 【雷達捕獲情報】\n{m['raw']}{nav_text}"
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=TARGET_CHAT_ID,
                messages=[TextMessage(text=msg_text)]
            )
        )

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

    if user_text in ["雷達", "查巨大", "巨大"]:
        if not live_mushrooms:
            reply = "目前暫無監聽到任何巨大特殊蘑菇情報！"
        else:
            lines = []
            for idx, m in enumerate(live_mushrooms[:5], start=1):
                loc = f"\n  座標：{m['lat']},{m['lng']}" if m['lat'] else ""
                lines.append(f"{idx}. {m['title']}{loc}")
            reply = "📡 【即時蘑菇情報清單】\n\n" + "\n\n".join(lines)
            
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )

# ==================== 3. 雙執行緒啟動 ====================
def start_discord_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(discord_client.start(DISCORD_TOKEN))

if __name__ == "__main__":
    # 開啟獨立背景執行緒運行 Discord 監聽
    if DISCORD_TOKEN:
    t = threading.Thread(target=start_discord_bot, daemon=True)
    t.start()
        
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
