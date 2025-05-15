from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (MessageEvent, TextMessage, TextSendMessage,
                            ImageMessage, AudioMessage, VideoSendMessage, FlexSendMessage)
import os
import re
import base64
import datetime
import pyodbc
from pydub import AudioSegment
import speech_recognition as sr
from openai import OpenAI
from dotenv import load_dotenv
from io import BytesIO

load_dotenv()

# ✅ LINE 憑證（請勿外洩）
LINE_CHANNEL_ACCESS_TOKEN = 'LpZLSJAqLsv3weSWeP4OgFuB2rAFgIOAR2RjZ/X4el1cIl3vLExeq6gU1iK08QaMNdj0pj3L51RohY50Re+d+6jhS7CVZwxcdl815P0tfcsofyiabYBRURAgW28phLDAOOWL8+6ijNuV7zHZ8qYMRQdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = '3db04cf933e7e95d73e2666867203573'
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# GPT client
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE")
)

flex_message_json = {
  "type": "bubble",
  "header": {
    "type": "box",
    "layout": "vertical",
    "contents": [
      {
        "type": "text",
        "text": "報修平台",
        "color": "#000000",
        "weight": "bold"
      }
    ]
  },
  "body": {
    "type": "box",
    "layout": "vertical",
    "spacing": "sm",
    "paddingAll": "10px",
    "contents": [
      {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "系統",
              "text": "系統"
            }
          },
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "螢幕",
              "text": "螢幕"
            }
          }
        ]
      },
      {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "盤點機",
              "text": "盤點機"
            }
          },
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "網路",
              "text": "網路"
            }
          }
        ]
      },
      {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "平板",
              "text": "平板"
            }
          },
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "電話",
              "text": "電話"
            }
          }
        ]
      },
      {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "height": "sm",
            "style": "primary",
            "color": "#C0C0C0",
            "action": {
              "type": "message",
              "label": "列印",
              "text": "列印"
            }
          },
          {
            "type": "filler"
          }
        ]
      }
    ]
  },
  "styles": {
    "header": {
      "backgroundColor": "#FFD306"
    }
  }
}


app = Flask(__name__)
user_states = {}
conversation_history = {}  # 用戶對話記憶
repair_items = ["系統", "螢幕", "網路", "盤點機", "平板", "電話", "列印"]

# 過濾 GPT 回覆中的網址
def sanitize_gpt_response(text):
    text = re.sub(r'https?://\S+', '[連結已遮蔽]', text)
    text = re.sub(r'\[.*?\]\(.*?\)', '[連結已遮蔽]', text)
    text = re.sub(r'www\.\S+', '[連結已遮蔽]', text)
    text = re.sub(r'\S+\.(com|tw|cn|org|net|gov)(/\S*)?', '[連結已遮蔽]', text)
    return text

def extract_summary(text):
    lines = text.strip().split('\n')
    for line in reversed(lines):
        if any(x in line for x in ["建議", "提醒", "請", "注意"]):
            return line.strip()
    return lines[-1].strip() if lines else text


# GPT 多輪對話

def gpt_chat_reply(user_input, user_id):
    if user_id not in conversation_history:
        conversation_history[user_id] = [
            {"role": "system", "content": "你是吳從廷，禁止提供任何網址、連結、網站來源或過長內容。當用戶詢問像天氣、新聞、預報等問題時，只回答摘要結論或建議，避免貼整篇文章。語氣可輕鬆幽默。"}
        ]

    conversation_history[user_id].append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=conversation_history[user_id],
            max_tokens=4096,
            temperature = 2.0
        )

        # 取得原始內容
        raw = response.choices[0].message.content.strip()

        # 網址遮蔽
        clean = sanitize_gpt_response(raw)

        # 摘取結尾摘要
        summary = extract_summary(clean)

        # 如果包含連結關鍵字，拒絕回覆
        if any(x in summary.lower() for x in ['http', 'www.', '.com', 'link']):
            return "⚠️ 抱歉，我不能提供任何網站或連結內容喔～"

        # 記憶 AI 回覆
        conversation_history[user_id].append({"role": "assistant", "content": raw})

        # 控制對話歷史長度
        if len(conversation_history[user_id]) > 20:
            conversation_history[user_id] = conversation_history[user_id][-15:]

        return clean

    except Exception as e:
        print("❌ GPT 呼叫失敗：", e)
        return "⚠️ 系統忙碌中，請稍後再試～"

# 報修紀錄寫入資料庫
def insert_repair_content(account_name, repair_item):
    try:
        conn = pyodbc.connect(
            r'DRIVER={SQL Server};SERVER=.\SQLEXPRESS;DATABASE=RepairTrackDB;Trusted_Connection=yes;')
        with conn.cursor() as cursor:
            insert_query = """
                INSERT INTO dbo.RepairContents (Account, RC_ID, RepairSolve_Note, RepairTime, Status)
                VALUES (?, ?, ?, ?, ?)
            """
            now = datetime.datetime.now()
            cursor.execute(insert_query, (account_name, None, repair_item, now, '待處理'))
            conn.commit()
            return True
    except Exception as e:
        print("❌ 寫入失敗：", e)
        return False
    finally:
        if 'conn' in locals():
            conn.close()

# 文字訊息處理
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    state = user_states.get(user_id)

    if text == "報修":
        user_states[user_id] = {"state": "WAITING_FOR_STORE_CODE"}
        reply = TextSendMessage(text="請輸入店櫃編號")

    elif isinstance(state, dict) and state.get("state") == "WAITING_FOR_STORE_CODE":
        try:
            conn = pyodbc.connect(r'DRIVER={SQL Server};SERVER=.\SQLEXPRESS;DATABASE=RepairTrackDB;Trusted_Connection=yes;')
            with conn.cursor() as cursor:
                cursor.execute("SELECT Counter_Number FROM dbo.Counter WHERE Counter_Code = ?", (text,))
                row = cursor.fetchone()
                if row:
                    account_name = row[0]
                    user_states[user_id] = {
                        "state": "WAITING_FOR_FAULT_SELECTION",
                        "store_code": text,
                        "account_name": account_name
                    }
                    reply = FlexSendMessage(alt_text="請選擇報修項目", contents=flex_message_json)
                else:
                    reply = TextSendMessage(text="❌ 查無此店櫃編號，請重新輸入")
        except Exception as e:
            print("❌ 查詢錯誤：", e)
            reply = TextSendMessage(text="⚠ 查詢時發生錯誤，請稍後再試")
        finally:
            if 'conn' in locals(): conn.close()

    elif isinstance(state, dict) and state.get("state") == "WAITING_FOR_FAULT_SELECTION":
        if text in repair_items:
            account_name = state.get("account_name")
            success = insert_repair_content(account_name, text)
            reply = TextSendMessage(text=f"✅ 已完成「{text}」項目的報修登記（{account_name}）")
            user_states.pop(user_id, None)
        else:
            reply = TextSendMessage(text="請從選單中點選有效報修項目。")

    elif text == "清除記憶":
        conversation_history.pop(user_id, None)
        reply = TextSendMessage(text="🧹 已清除記憶。您可以重新開始提問。")

    else:
        gpt_reply = gpt_chat_reply(text, user_id)
        reply = TextSendMessage(text=gpt_reply)

    line_bot_api.reply_message(event.reply_token, reply)

# 圖片處理 + GPT Vision
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    message_id = event.message.id
    image_content = line_bot_api.get_message_content(message_id).content

    with open("../temp.jpg", "wb") as f:
        f.write(image_content)

    with open("../temp.jpg", "rb") as image_file:
        image_data = base64.b64encode(image_file.read()).decode("utf-8")

    image_input = {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/jpeg;base64,{image_data}"
        }
    }

    try:
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": "請說明這張圖片的內容，協助判斷其用途或錯誤訊息。"},
                    image_input
                ]}
            ],
            max_tokens=500
        )
        gpt_result = response.choices[0].message.content.strip()
    except Exception as e:
        print("❌ GPT 圖像分析失敗：", e)
        gpt_result = "⚠️ 圖像 AI 分析失敗，請稍後再試～"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=gpt_result))

# 語音訊息處理（Whisper 模式）
@handler.add(MessageEvent, message=AudioMessage)
def handle_audio(event):
    message_id = event.message.id
    audio_content = line_bot_api.get_message_content(message_id).content
    with open("temp.m4a", "wb") as f:
        f.write(audio_content)
    AudioSegment.from_file("temp.m4a").export("temp.wav", format="wav")

    recognizer = sr.Recognizer()
    with sr.AudioFile("temp.wav") as source:
        audio = recognizer.record(source)
        try:
            text = recognizer.recognize_google(audio, language="zh-TW")
        except:
            text = "❌ 語音無法辨識"

    user_id = event.source.user_id
    gpt_reply = gpt_chat_reply(text, user_id)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=gpt_reply))

# 啟動伺服器
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

if __name__ == "__main__":
    app.run()