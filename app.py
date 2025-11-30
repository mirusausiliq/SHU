import os
import re
from io import BytesIO
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    ImageMessage,
    TextSendMessage,
)

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("Missing LINE bot credentials")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# key = chat_key（user / group / room）
pending_images = {}

# 5 位數字的 ID，例如 00001, 12345
ID_PATTERN = re.compile(r"^\d{5}$")


def get_chat_key(source) -> str | None:
    """
    把對話來源統一換成一個 key：
    - user: user:<user_id>
    - group: group:<group_id>
    - room: room:<room_id>
    方案 A 主要會用到 user:*
    """
    if hasattr(source, "user_id") and source.user_id:
        return f"user:{source.user_id}"
    if hasattr(source, "group_id") and source.group_id:
        return f"group:{source.group_id}"
    if hasattr(source, "room_id") and source.room_id:
        return f"room:{source.room_id}"
    return None


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event: MessageEvent):
    chat_key = get_chat_key(event.source)
    if chat_key is None:
        return

    # 記錄這張圖片，等待之後的 ID
    pending_images[chat_key] = {
        "message_id": event.message.id,
        "remaining": 3,   # 接下來 3 則訊息內等待 ID
    }

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="已記錄圖片，請在接下來 3 則訊息內輸入 5 位數 ID（如：00001）"
        )
    )


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event: MessageEvent):
    chat_key = get_chat_key(event.source)
    if chat_key is None:
        return

    text = event.message.text.strip()

    if chat_key not in pending_images:
        # 沒有 pending 圖，就忽略（或你也可以在這裡設計其他指令）
        return

    record = pending_images[chat_key]
    record["remaining"] -= 1

    # 如果這則訊息是 5 位數 ID
    if ID_PATTERN.match(text):
        image_bytes = download_image(record["message_id"])
        save_image(image_bytes, text)
        del pending_images[chat_key]

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"圖片已儲存為 {text}.jpg，感謝上傳！")
        )
        return

    # 如果不是 ID，又用完 3 則訊息，就放棄
    if record["remaining"] <= 0:
        del pending_images[chat_key]
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="三則訊息內未收到 5 位數 ID，已取消這張圖片的記錄。")
        )


def download_image(message_id: str) -> bytes:
    content = line_bot_api.get_message_content(message_id)
    buffer = BytesIO()
    for chunk in content.iter_content():
        buffer.write(chunk)
    return buffer.getvalue()


def save_image(image_bytes: bytes, image_id: str, folder: str = "images"):
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, f"{image_id}.jpg")
    with open(filepath, "wb") as f:
        f.write(image_bytes)
    print("Saved:", filepath)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
