import os
import re
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# 填入步驟 1 取得的憑證
CHANNEL_SECRET = "0f6c7d5c9921290c9dd87cdb83de9784"
CHANNEL_ACCESS_TOKEN = "Bppdi4+cXtgaEKyrEQMO5Tc2MwK+NxZiFNqVWupQPiGT2MTxfuBg5Ij9B0rFMaPr5CFuabOrj+x6T5BVVkyDU1kPxyflwRG7DNplH6Fv7cBgEb4mR5QRLjc/FYSnlgZHbh0Fs1fiG/UGKFIHgKN2ZgdB04t89/1O/w1cDnyilFU="

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# 簡易記憶體資料庫（儲存即時蘑菇資料）
active_mushrooms = []

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

    # 1. 查詢指令
    if user_text in ["查巨大", "巨大雷達", "雷達"]:
        if not active_mushrooms:
            reply = "目前雷達庫內暫無巨大蘑菇回報紀錄！"
        else:
            list_str = "\n".join([f"• {m['type']} - {m['name']}\n  座標：{m['lat']},{m['lng']}" for m in active_mushrooms[-5:]])
            reply = f"🍄 【近期回報巨大蘑菇】\n{list_str}"
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        return

    # 2. 通報格式：通報 巨大火 25.0339,121.5644 台北101
    match = re.match(r"^通報\s+(\S+)\s+([-\d.]+),([-\d.]+)\s+(.+)$", user_text)
    if match:
        m_type, lat, lng, name = match.groups()
        gmaps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        
        # 存入資料庫
        active_mushrooms.append({"type": m_type, "lat": lat, "lng": lng, "name": name})

        # 構建 LINE Flex Message 視覺化卡片
        flex_json = {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "🍄 巨大元素蘑菇警報", "weight": "bold", "color": "#ffffff", "size": "md"}
                ],
                "backgroundColor": "#D32F2F"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"{m_type}", "weight": "bold", "size": "xl"},
                    {"type": "separator", "margin": "md"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "lg",
                        "spacing": "sm",
                        "contents": [
                            {"type": "text", "text": f"📍 地標：{name}", "size": "sm", "wrap": True},
                            {"type": "text", "text": f"🌐 經緯度：{lat}, {lng}", "size": "sm", "weight": "bold"}
                        ]
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {
                            "type": "uri",
                            "label": "開啟 Google 地圖",
                            "uri": gmaps_url
                        }
                    }
                ]
            }
        }

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[FlexMessage(alt_text=f"通報：{m_type}", contents=FlexContainer.from_dict(flex_json))]
                )
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
