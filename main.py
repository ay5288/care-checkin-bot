import os, re, math, urllib.parse, requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
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
# ⚠️ 3. 請確認這個新機器人的 Render 公開網址（結尾不要加斜線）
RENDER_BASE_URL = "https://care-checkin-bot.onrender.com"


line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

CARE_MAP_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no"/>
<title>居家守護與周邊醫療地圖</title>
<style>
  body { margin: 0; padding: 0; font-family: -apple-system, sans-serif; background: #f2f4f8; }
  .header { background: #1DB446; color: #fff; padding: 12px 16px; font-weight: bold; font-size: 15px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
  .map-viewport { width: 100%; height: 320px; overflow: hidden; position: relative; background: #e2e8f0; border-bottom: 2px solid #cbd5e1; }
  .map-canvas { width: 768px; height: 768px; position: absolute; left: 50%; top: 50%; transform: translate(__OFFSET_X__px, __OFFSET_Y__px); }
  .tile-grid { display: grid; grid-template-columns: repeat(3, 256px); grid-template-rows: repeat(3, 256px); width: 768px; height: 768px; }
  .tile-grid img { width: 256px; height: 256px; display: block; }
  .overlay-svg { position: absolute; top: 0; left: 0; width: 768px; height: 768px; pointer-events: none; }
  .legend { background: #fff; text-align: center; font-size: 12px; color: #64748b; padding: 8px 0; border-bottom: 1px solid #e5e7eb; }
  .box { padding: 14px; max-width: 600px; margin: 0 auto; }
  .card { background: #fff; border-radius: 12px; padding: 16px; margin-bottom: 12px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); }
  .title { font-weight: bold; font-size: 16px; color: #111; margin-bottom: 6px; }
  .meta { font-size: 13px; color: #555; line-height: 1.5; margin-bottom: 12px; }
  .btn { display: block; width: 100%; box-sizing: border-box; text-align: center; text-decoration: none; padding: 13px; border-radius: 10px; font-size: 15px; font-weight: bold; color: #fff; margin-bottom: 10px; -webkit-tap-highlight-color: rgba(0,0,0,0.1); }
  .btn-red { background: #FF3B30; }
  .btn-green { background: #1DB446; }
  .btn-blue { background: #007AFF; }
</style>
</head><body>
<div class="header">
  <span>🛡️ 最後平安位置與醫療急救</span>
  <span style="font-size:12px;opacity:0.9;">台灣官方街道圖</span>
</div>
<div class="map-viewport">
  <div class="map-canvas">
    <div class="tile-grid">__TILES__</div>
    <svg class="overlay-svg" viewBox="0 0 768 768">__SVG_MARKS__</svg>
  </div>
</div>
<div class="legend">🟢 最後登記平安位置（方圓 1km 守護圈）</div>
<div class="box">
  <div class="card">
    <div class="title">📍 登記地址與座標</div>
    <div class="meta">__ADDR__<br>座標：__LAT__, __LON__</div>
    <a class="btn btn-blue" href="__NAV_URL__">🧭 導航前往此登記位置</a>
  </div>
  <div class="card">
    <div class="title">🏥 周邊 500m～1.5km 緊急醫療與藥局</div>
    <div class="meta">點擊下方按鈕，直接在地圖顯示距離此位置最近的急診室、健保藥局與派出所：</div>
    <a class="btn btn-red" href="__HOSP_URL__">🏥 搜尋附近急診醫院</a>
    <a class="btn btn-green" href="__PHARM_URL__">💊 搜尋附近健保藥局</a>
    <a class="btn btn-blue" href="__POL_URL__">🚓 搜尋附近警察局／派出所</a>
  </div>
</div>
</body></html>"""
def latlon_to_pixel(lat, lon, z=16):
    n = 2.0 ** z
    x = (float(lon) + 180.0) / 360.0 * n * 256.0
    lat_rad = math.radians(float(lat))
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n * 256.0
    return x, y

def parse_latlon(loc_str: str):
    try:
        if "(" in loc_str and ")" in loc_str:
            part = next(iter(loc_str.split("(")[-1].split(")")))
            lat_s, lon_s = part.split(",")
            return float(lat_s.strip()), float(lon_s.strip())
    except Exception:
        pass
    return None, None

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

@app.get("/care_map", response_class=HTMLResponse)
def care_map_view(lat: float, lon: float, addr: str = "登記位置"):
    S = chr(47)
    z = 16
    ux, uy = latlon_to_pixel(lat, lon, z)
    cx_tile, cy_tile = int(ux // 256), int(uy // 256)
    origin_x, origin_y = (cx_tile - 1) * 256, (cy_tile - 1) * 256

    tiles_html = []
    for dy in [-1, 0, 1]:
        for dx in [-1, 0, 1]:
            tx, ty = cx_tile + dx, cy_tile + dy
            nlsc_url = f"https:{S}{S}wmts.nlsc.gov.tw{S}wmts{S}EMAP{S}default{S}GoogleMapsCompatible{S}{z}{S}{ty}{S}{tx}"
            tiles_html.append(f'<img src="{nlsc_url}"/>')

    user_px, user_py = ux - origin_x, uy - origin_y
    offset_x, offset_y = round(-user_px, 1), round(-user_py, 1)
    meters_per_px = 156543.03392 * math.cos(math.radians(float(lat))) / (2 ** z)
    radius_px = round(1000.0 / meters_per_px, 1)

    svg_marks = [
        f'<circle cx="{user_px}" cy="{user_py}" r="{radius_px}" fill="#1DB446" fill-opacity="0.12" stroke="#1DB446" stroke-width="2" stroke-dasharray="6,4"/>',
        f'<circle cx="{user_px}" cy="{user_py}" r="10" fill="#1DB446" stroke="#fff" stroke-width="3"/>'
    ]
    safe_addr = addr.replace("<", "").replace(">", "")
    host = "maps." + "google.com"
    base = f"https:{S}{S}{host}{S}"
    nav_url = f"{base}?daddr={lat},{lon}&dirflg=w"
    hosp_url = f"{base}?q={urllib.parse.quote('急診醫院')}&ll={lat},{lon}&z=15"
    pharm_url = f"{base}?q={urllib.parse.quote('健保藥局')}&ll={lat},{lon}&z=15"
    pol_url = f"{base}?q={urllib.parse.quote('派出所')}&ll={lat},{lon}&z=15"

    html = (CARE_MAP_HTML
            .replace("__OFFSET_X__", str(offset_x))
            .replace("__OFFSET_Y__", str(offset_y))
            .replace("__TILES__", "".join(tiles_html))
            .replace("__SVG_MARKS__", "".join(svg_marks))
            .replace("__ADDR__", safe_addr)
            .replace("__LAT__", str(lat))
            .replace("__LON__", str(lon))
            .replace("__NAV_URL__", nav_url)
            .replace("__HOSP_URL__", hosp_url)
            .replace("__PHARM_URL__", pharm_url)
            .replace("__POL_URL__", pol_url))
    return HTMLResponse(content=html)
def make_medical_card(uname: str, med_info: str, loc_str: str, phone: str = "尚未設定"):
    S = chr(47)
    edit_med_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('醫療:')}"
    edit_phone_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('電話:')}"

    footer_btns = [
        ButtonComponent(style="primary", color="#FF3B30", height="sm", action=URIAction(label="🚨 緊急撥打 119 救護專線", uri="tel:119")),
        ButtonComponent(style="primary", color="#007AFF", height="sm", action=URIAction(label="✏️ 輸入／修改醫療資料", uri=edit_med_uri)),
        ButtonComponent(style="secondary", height="sm", action=URIAction(label="📱 登記／修改本人電話（供警報通報）", uri=edit_phone_uri)),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="📋 返回我的守護狀態", text="我的狀態"))
    ]

    return BubbleContainer(
        size="mega",
        header=BoxComponent(layout="vertical", background_color="#FF3B30", padding_all="14px", contents=[
            TextComponent(text="🪪 隨身緊急醫療卡（ICE 急救資訊）", weight="bold", size="md", color="#FFFFFF", wrap=True)
        ]),
        body=BoxComponent(layout="vertical", spacing="md", contents=[
            TextComponent(text=f"持卡人：{uname}　｜　本人電話：{phone}", size="sm", weight="bold", color="#111111", wrap=True),
            BoxComponent(layout="vertical", background_color="#FFF5F5", corner_radius="8px", padding_all="12px", contents=[
                TextComponent(text="📋 血型／過敏史／病史／常去醫院：", size="xs", weight="bold", color="#FF3B30"),
                TextComponent(text=med_info, size="sm", color="#222222", margin="sm", wrap=True)
            ]),
            TextComponent(text=f"📍 登記居住地：{loc_str}", size="xs", color="#666666", wrap=True),
            TextComponent(
                text="💡 說明：登記的「本人電話」將於觸發失聯警報時，附帶一鍵直撥按鈕發送給您的緊急聯絡人。",
                size="xxs",
                color="#666666",
                margin="md",
                wrap=True
            )
        ]),
        footer=BoxComponent(layout="vertical", spacing="sm", contents=footer_btns)
    )


def make_main_card(title: str, subtitle: str, status_text: str, uid: str, lat=None, lon=None, addr="最後平安位置"):
    S = chr(47)
    bind_cmd = urllib.parse.quote(f"綁定:{uid}")
    share_text = urllib.parse.quote(f"我正在使用「獨居平安簽到助手」每天報平安！請點擊下方連結成為我的緊急聯絡人（最多可綁定 5 位親友）：\nhttps:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{bind_cmd}")
    share_uri = f"https:{S}{S}line.me{S}R{S}share?text={share_text}"
    loc_uri = f"https:{S}{S}line.me{S}R{S}nv{S}location{S}"

    footer_btns = [
        ButtonComponent(style="primary", color="#1DB446", height="sm", action=MessageAction(label="🟢 我今天很好（一鍵簽到）", text="我今天很好")),
        ButtonComponent(style="primary", color="#007AFF", height="sm", action=URIAction(label="👨‍👩‍👧 邀請家人（最多綁定5位）", uri=share_uri)),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="🪪 緊急醫療卡／登記電話", text="醫療卡")),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="💊 設定每日用藥提醒", text="用藥提醒"))
    ]
    if lat is not None and lon is not None:
        enc_addr = urllib.parse.quote(addr)
        map_uri = f"{RENDER_BASE_URL}/care_map?lat={lat}&lon={lon}&addr={enc_addr}&openExternalBrowser=1"
        footer_btns.append(ButtonComponent(style="primary", color="#FF3B30", height="sm", action=URIAction(label="🏥 最後位置與周邊藥局／急診", uri=map_uri)))
    footer_btns.append(ButtonComponent(style="secondary", height="sm", action=URIAction(label="📍 更新我的居住位置", uri=loc_uri)))

    return BubbleContainer(
        size="mega",
        header=BoxComponent(layout="vertical", background_color="#1DB446", padding_all="14px", contents=[
            TextComponent(text=title, weight="bold", size="md", color="#FFFFFF", wrap=True)
        ]),
        body=BoxComponent(layout="vertical", spacing="md", contents=[
            TextComponent(text=subtitle, size="sm", weight="bold", color="#111111", wrap=True),
            BoxComponent(layout="vertical", background_color="#F5F7FA", corner_radius="8px", padding_all="12px", contents=[
                TextComponent(text=status_text, size="xs", color="#333333", wrap=True)
            ]),
            TextComponent(text="⏱️ 快速切換通報週期（含 2 小時本人緩衝預警）：", size="xxs", color="#888888", wrap=True),
            BoxComponent(layout="horizontal", spacing="sm", contents=[
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="12h", text="設定12")),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="24h", text="設定24")),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="48h", text="設定48"))
            ])
        ]),
        footer=BoxComponent(layout="vertical", spacing="sm", contents=footer_btns)
    )
@app.get("/")
def root():
    return {"status": "Online", "service": "Solo Care Check-in Bot Full Version"}

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
        f"🕒 首次簽到時間：{t_str}\n⏰ 目前守護週期：24 小時（逾時先提醒本人，2 小時後通知聯絡人）\n\n💡 請先傳送居住位置並填寫「緊急醫療卡／登記電話」，最多可邀請 5 位家人成為緊急聯絡人！",
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
        "📍 居住位置與醫療地圖已開通",
        "已將此位置設為您的最後平安參考座標：",
        f"🏠 登記地址：{addr}\n🏥 點擊下方紅色按鈕，可立即查看此位置的台灣官方街道圖，並一鍵搜尋周邊急診醫院與健保藥局！",
        uid, lat=lat, lon=lon, addr=addr
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="居住位置更新成功", contents=card))

@handler.add(MessageEvent, message=TextMessage)
def on_text(event):
    uid = event.source.user_id
    uname = get_user_name(uid)
    txt = event.message.text.strip()

    if txt in ["醫療卡", "緊急醫療卡", "急救卡"]:
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        med = st.get("medical_info", "尚未填寫")
        loc = st.get("last_location", "尚未設定")
        phone = st.get("phone", "尚未設定")
        m_card = make_medical_card(uname, med, loc, phone)
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="隨身緊急醫療卡", contents=m_card))
        return

    if txt.startswith("醫療:") or txt.startswith("醫療："):
        med_content = txt[3:].strip()
        if not med_content:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 請在「醫療:」後面輸入您的血型、過敏藥物或慢性病史喔！\n例如：醫療:O型，高血壓，無藥物過敏，常去雙和醫院"))
            return
        call_sheet({"action": "set_medical", "user_id": uid, "user_name": uname, "medical_info": med_content})
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        loc = st.get("last_location", "尚未設定")
        phone = st.get("phone", "尚未設定")
        m_card = make_medical_card(uname, med_content, loc, phone)
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="緊急醫療卡更新成功", contents=m_card))
        return

    if txt.startswith("電話:") or txt.startswith("電話："):
        phone_val = txt[3:].strip()
        if not phone_val:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 請在「電話:」後面輸入您的手機或市話號碼喔！\n例如：電話:0912345678"))
            return
        call_sheet({"action": "set_phone", "user_id": uid, "user_name": uname, "phone": phone_val})
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        med = st.get("medical_info", "尚未填寫")
        loc = st.get("last_location", "尚未設定")
        m_card = make_medical_card(uname, med, loc, phone_val)
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="聯絡電話登記成功", contents=m_card))
        return

    if txt in ["用藥提醒", "吃藥提醒", "設定吃藥"]:
        S = chr(47)
        med_cmd = urllib.parse.quote("吃藥:")
        custom_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{med_cmd}"
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        cur_times = st.get("med_times", "尚未設定")
        rem_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#007AFF", padding_all="14px", contents=[
                TextComponent(text="💊 每日定時用藥提醒（兼報平安）", weight="bold", size="md", color="#FFFFFF", wrap=True)
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                TextComponent(text=f"目前設定時段：{cur_times}", size="sm", weight="bold", color="#111111", wrap=True),
                TextComponent(text="支援設定多組「小時:分鐘」（例如 08:30, 12:30, 20:00）。時間一到系統會自動推播提醒卡片，點擊「✅ 已服藥」即可順便完成今日平安簽到！", size="xs", color="#555555", wrap=True),
                TextComponent(text="⚡ 一鍵套用常見多時段：", size="xxs", color="#888888"),
                BoxComponent(layout="horizontal", spacing="sm", contents=[
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label="08:00", text="吃藥:08:00")),
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label="早晚8點", text="吃藥:08:00, 20:00")),
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label="三餐飯後", text="吃藥:08:30, 12:30, 18:30"))
                ])
            ]),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=[
                ButtonComponent(style="primary", color="#007AFF", height="sm", action=URIAction(label="✏️ 自訂多時段（如 08:30, 21:15）", uri=custom_uri)),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="🔕 關閉用藥提醒", text="吃藥:關閉"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="每日定時用藥提醒設定", contents=rem_card))
        return

    if txt.startswith("吃藥:") or txt.startswith("吃藥："):
        t_val = txt[3:].strip().replace("，", ", ").replace("：", ":")
        call_sheet({"action": "set_med_times", "user_id": uid, "user_name": uname, "med_times": t_val})
        msg = "🔕 已為您關閉每日定時用藥提醒。" if t_val == "關閉" else f"💊 設定成功！系統將於每天【{t_val}】準時推播用藥提醒卡片給您，點擊「✅ 已服藥」即可順便完成今日平安簽到！"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return
    if txt in ["教學", "說明", "使用教學", "怎麼用", "幫助", "?"]:
        guide_card = make_main_card(
            "📖 獨居平安簽到助手 · 使用教學",
            "每天花 1 秒鐘報平安，守護您的居家安全：",
            "1. 每日簽到：點綠色按鈕重新計算守護時間。\n2. 緊急醫療卡與電話：點「醫療卡」登記血型、病史與聯絡電話，失聯警報時自動附上「一鍵直撥電話」按鈕給聯絡人。\n3. 用藥提醒：支援設定多組「小時:分鐘」，服藥時順便報平安。\n4. 多位家人守護：最多可綁定 5 位緊急聯絡人，失聯時同步通知所有家人！",
            uid
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="獨居平安簽到助手使用教學", contents=guide_card))
        return

    if txt in ["設定12", "設定24", "設定48"]:
        hours = int(txt.replace("設定", ""))
        call_sheet({"action": "set_hours", "user_id": uid, "user_name": uname, "hours": hours})
        card = make_main_card(
            f"⏰ 通報週期已更新為 {hours} 小時",
            f"{uname}，您的守護規則已調整完成！",
            f"🔔 新規則：若超過 {hours} 小時未簽到，系統將先傳送提醒給您；若再過 2 小時仍無回應，將自動通報所有已綁定的緊急聯絡人並附上緊急醫療卡與直撥電話。",
            uid
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"通報週期已設為{hours}小時", contents=card))
        return

    if txt.startswith("綁定:"):
        elder_uid = txt[3:].strip()
        if elder_uid == uid:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 不能綁定自己為緊急聯絡人喔！請把邀請卡片轉傳給您的家人或朋友點擊。"))
            return
        res = call_sheet({"action": "bind", "user_id": elder_uid, "contact_id": uid})
        st_bind = res.get("status", "")
        elder_name = res.get("elder_name", "您的親友")
        c_num = res.get("count", 1)

        if st_bind == "full":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 「{elder_name}」的緊急聯絡人已達 5 位上限囉！如欲調整請聯繫對方。"))
            return

        title_msg = "✅ 您已是守護聯絡人之一！" if st_bind == "already_bound" else "🤝 緊急聯絡人綁定成功！"
        bind_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#007AFF", padding_all="14px", contents=[
                TextComponent(text=title_msg, weight="bold", size="md", color="#FFFFFF")
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                TextComponent(text=f"您已成為「{elder_name}」的第 {c_num} 位平安守護聯絡人（上限 5 位）！", size="sm", weight="bold", color="#111111", wrap=True),
                TextComponent(text="當對方超過設定時間未簽到時，系統會同步通知所有已綁定的聯絡人並附上直撥電話按鈕。平時您也可以隨時點擊下方按鈕（或輸入「關心」），傳送暖心問候提醒對方報平安！", size="xs", color="#555555", wrap=True)
            ]),
            footer=BoxComponent(layout="vertical", contents=[
                ButtonComponent(style="primary", color="#FF9500", height="sm", action=MessageAction(label="💌 立即傳送關心問候", text="關心"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="緊急聯絡人綁定成功", contents=bind_card))
        return

    if txt in ["關心", "傳送關心", "問候"]:
        res = call_sheet({"action": "care", "user_id": uid, "contact_id": uid, "sender_name": uname})
        if res.get("status") == "care_sent":
            elders = res.get("elders", "您的親友")
            reply_card = BubbleContainer(
                size="mega",
                body=BoxComponent(layout="vertical", spacing="sm", contents=[
                    TextComponent(text="💌 暖心問候已送達！", weight="bold", size="md", color="#FF9500"),
                    TextComponent(text=f"已為您傳送關心卡片給「{elders}」，對方點擊卡片按鈕後即可順便完成今日平安簽到！", size="xs", color="#333333", wrap=True)
                ]),
                footer=BoxComponent(layout="vertical", contents=[
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label="💌 再次傳送關心", text="關心"))
                ])
            )
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="暖心問候已送達", contents=reply_card))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💡 您目前尚未成為任何親友的緊急聯絡人喔！請請您的家人點擊他帳號裡的「👨‍👩‍👧 邀請家人（最多綁定5位）」並傳送連結給您即可完成綁定。"))
        return

    if txt in ["狀態", "查詢", "我的狀態", "設定"]:
        res = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        c_cnt = res.get("contact_count", 0)
        c_status = f"✅ 已綁定 {c_cnt} / 5 位緊急聯絡人" if c_cnt > 0 else "⚠️ 尚未綁定聯絡人（請點下方藍色按鈕邀請）"
        cur_h = res.get("alert_hours", 24)
        loc_str = res.get("last_location", "尚未設定")
        med_str = res.get("medical_info", "尚未填寫")
        med_t = res.get("med_times", "尚未設定")
        phone_str = res.get("phone", "尚未設定")
        lat, lon = parse_latlon(loc_str)
        clean_addr = loc_str.split("(")[0].strip() if "(" in loc_str else "最後平安位置"
        card = make_main_card(
            "📋 我的平安守護狀態",
            f"守護對象：{uname}",
            f"🕒 最後簽到：{res.get('last_checkin', '無紀錄')}\n⏰ 通報週期：{cur_h} 小時（含 2 小時緩衝）\n💊 用藥提醒：{med_t}\n👨‍👩‍👧 聯絡人狀態：{c_status}\n📞 登記電話：{phone_str}\n🪪 緊急醫療卡：{med_str}\n📍 最後平安位置：{loc_str}",
            uid, lat=lat, lon=lon, addr=clean_addr
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="我的平安守護狀態", contents=card))
        return

    res = call_sheet({"action": "checkin", "user_id": uid, "user_name": uname})
    t_str = res.get("time", "剛剛")
    cur_h = res.get("alert_hours", 24)
    c_cnt = res.get("contact_count", 0)
    c_note = f"✅ {c_cnt} 位緊急聯絡人守護中" if c_cnt > 0 else "⚠️ 提醒：您尚未綁定緊急聯絡人，請點下方藍色按鈕傳送邀請給家人！"
    st_info = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
    loc_str = st_info.get("last_location", "")
    lat, lon = parse_latlon(loc_str)
    clean_addr = loc_str.split("(")[0].strip() if "(" in loc_str else "最後平安位置"
    card = make_main_card(
        "✅ 今日平安簽到成功！",
        f"{uname}，很高興知道您今天一切安好！",
        f"🕒 簽到時間：{t_str}\n⏰ 守護週期：{cur_h} 小時（含 2 小時緩衝）\n🔔 守護狀態：{c_note}",
        uid, lat=lat, lon=lon, addr=clean_addr
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="今日平安簽到成功", contents=card))
