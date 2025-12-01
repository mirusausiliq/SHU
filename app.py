import os
import re
import sys
import json
from io import BytesIO
from datetime import datetime

# 導入 Line Bot 相關函式庫
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    ImageMessage,
    TextSendMessage,
)

# --- Google Drive 相關導入 (您需要在您的環境中安裝並設定) ---
# 為了讓此程式碼在沒有安裝 Google API Client 的情況下仍可運行，我們先註釋掉實際導入。
# 實際部署時，請確保這些函式庫已安裝並配置好認證。
try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    GOOGLE_DRIVE_SERVICE = None
except ImportError:
    print("警告: Google Drive 函式庫未安裝，上傳功能將無法運行。請參考 SETUP_GUIDE.md 進行安裝。")


app = Flask(__name__)

# 從環境變數獲取 Line Bot 憑證
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    # 確保應用程式不會啟動，並明確指出缺少憑證
    print("錯誤: 缺少 LINE Bot 憑證 (LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET)。")
    sys.exit(1)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# key = chat_key（user / group / room）
pending_images = {}

# 5 位數字的 ID（銀行帳戶後五碼），例如 00001, 12345
ID_PATTERN = re.compile(r"^\d{5}$")

# TODO: 步驟 3: 放置您的 Google Drive 目標資料夾 ID (選填，若不填則存到您的 My Drive 根目錄)
DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", None)

# --- 輔助函式 ---

def build_drive_service():
    """
    初始化 Google Drive Service 物件 (需要 Service Account 認證)
    TODO: 您需要替換為適合您 Zeabur 環境的認證方式
    """
    try:
        # 假設您使用 Service Account 認證，並將金鑰 JSON 存為環境變數 GOOGLE_SERVICE_ACCOUNT_CREDENTIALS
        credentials_info = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"))
        credentials = Credentials.from_service_account_info(
            credentials_info,
            scopes=['https://www.googleapis.com/auth/drive.file'] # 最小化權限
        )
        service = build('drive', 'v3', credentials=credentials)
        print("Google Drive Service 初始化成功。")
        return service
    except Exception as e:
        print(f"Google Drive Service 初始化失敗: {e}")
        return None

# # 在應用程式啟動時初始化 Google Drive Service (在實際部署中推薦這樣做)
GOOGLE_DRIVE_SERVICE = build_drive_service()


def get_chat_key(source) -> str | None:
    """
    把對話來源統一換成一個 key
    """
    if hasattr(source, "user_id") and source.user_id:
        return f"user:{source.user_id}"
    if hasattr(source, "group_id") and source.group_id:
        return f"group:{source.group_id}"
    if hasattr(source, "room_id") and source.room_id:
        return f"room:{source.room_id}"
    return None


def download_image(message_id: str) -> bytes:
    """
    從 Line 獲取圖片的位元組資料
    """
    content = line_bot_api.get_message_content(message_id)
    buffer = BytesIO()
    for chunk in content.iter_content():
        buffer.write(chunk)
    return buffer.getvalue()


def upload_image_to_google_drive(image_bytes: bytes, bank_tail: str):
    """
    根據新規則構建檔名並上傳至 Google Drive。
    檔名格式: YYYYMMDD_XXXXX.jpg
    """
    today_date = datetime.now().strftime("%Y%m%d")
    filename = f"{today_date}_{bank_tail}.jpg"

    # 模擬上傳，因為我們無法在當前環境中執行實際的 Google API 呼叫
    # 在您的實際部署中，請替換此處的模擬程式碼
    print(f"嘗試上傳圖片，檔名: {filename}")
    print("--- 執行 Google Drive API 上傳程式碼 (需替換) ---")

    if GOOGLE_DRIVE_SERVICE:
        try:
            media = MediaIoBaseUpload(BytesIO(image_bytes), mimetype='image/jpeg', chunksize=-1, resumable=True)
            file_metadata = {
                'name': filename,
                # 如果有資料夾 ID，則將其加入 parents
                'parents': [DRIVE_FOLDER_ID] if DRIVE_FOLDER_ID else []
            }

            file = GOOGLE_DRIVE_SERVICE.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            print(f"成功上傳至 Google Drive，檔名: {filename}，檔案 ID: {file.get('id')}")
            return filename
        except Exception as e:
            print(f"上傳 Google Drive 失敗: {e}")
            return None
    else:
        print("Google Drive 服務未初始化或初始化失敗。無法上傳。")
        return None # 返回 None 表示失敗

    # 模擬成功 (請移除此行和之前的 print 後，再取消註釋上方的實際程式碼)
    # return filename

# --- Line Bot Webhook 處理 ---

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Webhook 簽名驗證失敗，請求被拒絕。")
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event: MessageEvent):
    """
    處理使用者傳送圖片的事件
    """
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
            text="已接收圖片！請在接下來 3 則訊息內，立即輸入 5 位數 ID（例如：00001）以完成上傳。"
        )
    )


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event: MessageEvent):
    """
    處理使用者傳送文字訊息的事件，用於接收 5 位數 ID
    """
    chat_key = get_chat_key(event.source)
    if chat_key is None:
        return

    text = event.message.text.strip()

    if chat_key not in pending_images:
        # 沒有 pending 圖，忽略
        return

    record = pending_images[chat_key]
    record["remaining"] -= 1

    # 如果這則訊息是 5 位數 ID (銀行帳戶後五碼)
    if ID_PATTERN.match(text):
        try:
            # 1. 下載圖片
            image_bytes = download_image(record["message_id"])

            # 2. 上傳至 Google Drive (包含命名邏輯: YYYYMMDD_XXXXX.jpg)
            uploaded_filename = upload_image_to_google_drive(image_bytes, text)

            del pending_images[chat_key]

            if uploaded_filename:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"圖片已成功上傳至 Google Drive！\n檔名為：{uploaded_filename}")
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="圖片上傳至 Google Drive 失敗，請檢查 Line Bot 後臺日誌與 Google Drive API 設定。")
                )
        except Exception as e:
            print(f"處理訊息時發生錯誤: {e}")
            del pending_images[chat_key]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="處理過程中發生錯誤，已取消本次上傳。")
            )
        return

    # 如果不是 ID，又用完 3 則訊息，就放棄
    if record["remaining"] <= 0:
        del pending_images[chat_key]
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="三則訊息內未收到 5 位數 ID，本次圖片記錄已取消。請重新傳送圖片。")
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)