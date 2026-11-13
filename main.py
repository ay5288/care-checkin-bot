import os, urllib.parse, requests
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, FollowEvent, TextMessage, LocationMessage, 
    TextSendMessage, FlexSendMessage, BubbleContainer, 
    BoxComponent, TextComponent, ButtonComponent, URIAction, MessageAction
)

app = FastAPI()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# ⚠️ 1. 請填入「獨居平安簽到資料庫」的 Google Apps Script /exec 網址
GOOGLE_SHEET_API = "https://script.google.com/macros/s/AKfycbwgRi22jzN5tNOiNHM-RdtW0U0X06Hkm44___Vj8bzewfsUD0tMVd-AbMeqRvFiajna/exec"
# ⚠️ 2. 請填入新機器人的 LINE 官方帳號 ID（包含 @ 符號，例如 @123abcde）
LINE_BOT_BASIC_ID = "@189gmqxi"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

def call_sheet(payload: dict):
    try:
        r = requests.post(GOOGLE_SHEET_API, json=payload, timeout=12)
        return r.json()
    except Exception:
        return {}

def get_user_name(uid: str):
    try:
        profile = line_bot_api.get_profile(uid)
        return profile.display_name
    except Exception:
        return "用戶"

def make_main_card(title: str, subtitle: str, status_text: str, uid: str):
    S = chr(47)
    bind_cmd = urllib.parse.quote(f"綁定:{uid}")
    share_text = urllib.parse.quote(f"我正在使用「獨居平安簽到助手」每天報平安！請點擊下方連結成為我的緊急聯絡人：\nhttps:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{bind_cmd}")
    share_uri = f"https:{S}{S}line.me{S}R{S}share?text={share_text}"
    loc_uri = f"https:{S}{S}line.me{S}R{S}nv{S}location{S}"

    return BubbleContainer(
        size="mega",
        header=BoxComponent(layout="vertical", backgroundColor="#1DB446", contents=[
            TextComponent(text=title, weight="bold", size="md", color="#FFFFFF")
        ]),
        body=BoxComponent(layout="vertical", spacing="md", contents=[
            TextComponent(text=subtitle, size="sm", weight="bold", color="#111111", wrap=True),
            BoxComponent(layout="vertical", backgroundColor="#F5F7FA", cornerRadius="8px", paddingAll="12px", contents=[
                TextComponent(text=status_text, size="xs", color="#333333", wrap=True)
            ])
        ]),
        footer=BoxComponent(layout="vertical", spacing="sm", contents=[
            ButtonComponent(style="primary", color="#1DB446", height="sm", action=MessageAction(label="🟢 我今天很好（一鍵簽到）", text="我今天很好")),
            ButtonComponent(style="primary", color="#007AFF", height="sm", action=URIAction(label="👨‍👩‍👧 邀請家人成為緊急聯絡人", uri=share_uri)),
            ButtonComponent(style="secondary", height="sm", action=URIAction(label="📍 更新我的居住位置", uri=loc_uri))
        ])
    )
@app.get("/")
def root():
    return {"status": "Online", "service": "Solo Care Check-in Bot"}

@app.post("/callback")
async def callback(request: Request):
    sig = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), sig)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(FollowEvent)
def on_follow(event):
    uid = event.source.user_id
    uname = get_user_name(uid)
    res = call_sheet({"action": "checkin", "user_id": uid, "user_name": uname})
    t_str = res.get("time", "剛剛")
    card = make_main_card(
        "🛡️ 歡迎使用獨居平安簽到助手",
        f"嗨 {uname}！已為您完成首次平安建檔。",
        f"🕒 首次簽到時間：{t_str}\n⏰ 警報規則：超過 24 小時未簽到，系統將自動通知您的緊急聯絡人。\n\n💡 請先點擊下方藍色按鈕，將邀請傳送給家人或好友完成綁定！",
        uid
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="歡迎使用獨居平安簽到助手", contents=card))

@handler.add(MessageEvent, message=LocationMessage)
def on_location(event):
    uid = event.source.user_id
    uname = get_user_name(uid)
    addr = event.message.address or "已定位座標"
    lat, lon = event.message.latitude, event.message.longitude
    call_sheet({"action": "location", "user_id": uid, "user_name": uname, "address": addr, "lat": lat, "lon": lon})
    card = make_main_card(
        "📍 居住位置更新成功",
        "已將此位置設為您的緊急通報參考地址：",
        f"🏠 登記地址：{addr}\n（若超過 24 小時未簽到，此位置將隨警報一併發送給緊急聯絡人）",
        uid
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="居住位置更新成功", contents=card))

@handler.add(MessageEvent, message=TextMessage)
def on_text(event):
    uid = event.source.user_id
    uname = get_user_name(uid)
    txt = event.message.text.strip()

    # 1. 緊急聯絡人點擊邀請連結後的自動雙向綁定
    if txt.startswith("綁定:"):
        elder_uid = txt[3:].strip()
        if elder_uid == uid:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 不能綁定自己為緊急聯絡人喔！請把邀請卡片轉傳給您的家人或朋友點擊。"))
            return
        res = call_sheet({"action": "bind", "user_id": elder_uid, "contact_id": uid})
        elder_name = res.get("elder_name", "您的親友")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤝 【緊急聯絡人綁定成功】\n\n您已正式成為「{elder_name}」的平安守護聯絡人！\n當對方超過 24 小時未點擊平安簽到時，系統會第一時間透過此 LINE 帳號通知您。"))
        return

    # 2. 查詢目前平安狀態
    if txt in ["狀態", "查詢", "我的狀態", "設定"]:
        res = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        c_status = "✅ 已綁定緊急聯絡人" if res.get("has_contact") else "⚠️ 尚未綁定聯絡人（請點下方藍色按鈕邀請）"
        card = make_main_card(
            "📋 我的平安守護狀態",
            f"守護對象：{uname}",
            f"🕒 最後簽到：{res.get('last_checkin', '無紀錄')}\n👨‍👩‍👧 聯絡人狀態：{c_status}\n📍 登記位置：{res.get('last_location', '尚未設定')}",
            uid
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="我的平安守護狀態", contents=card))
        return

    # 3. 預設任何問候或點擊「我今天很好」皆執行每日平安簽到
    res = call_sheet({"action": "checkin", "user_id": uid, "user_name": uname})
    t_str = res.get("time", "剛剛")
    c_note = "✅ 緊急聯絡人守護中" if res.get("has_contact") else "⚠️ 提醒：您尚未綁定緊急聯絡人，請點下方藍色按鈕傳送邀請給家人！"
    card = make_main_card(
        "✅ 今日平安簽到成功！",
        f"{uname}，很高興知道您今天一切安好！",
        f"🕒 簽到時間：{t_str}\n🔔 守護狀態：{c_note}",
        uid
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="今日平安簽到成功", contents=card))
