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
def make_medical_card(uname: str, med_info: str, loc_str: str, phone: str = "尚未設定", ice_contact: str = "尚未設定"):
    S = chr(47)
    edit_med_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('醫療:')}"
    edit_ice_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('緊急聯絡人:')}"
    edit_addr_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('地址:')}"
    edit_phone_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('電話:')}"

    ice_phone_match = re.search(r"0\d[\d\-]{7,11}", str(ice_contact or ""))
    clean_ice_phone = re.sub(r"[^0-9]", "", ice_phone_match.group(0)) if ice_phone_match else ""

    footer_btns = []
    if len(clean_ice_phone) >= 8:
        footer_btns.append(
            ButtonComponent(style="primary", color="#1DB446", height="sm", action=URIAction(label=f"📞 直撥緊急聯絡人 ({clean_ice_phone})", uri=f"tel:{clean_ice_phone}"))
        )
    footer_btns.extend([
        ButtonComponent(style="primary", color="#FF3B30", height="sm", action=URIAction(label="🚨 緊急撥打 119 救護專線", uri="tel:119")),
        ButtonComponent(style="primary", color="#007AFF", height="sm", action=URIAction(label="👨‍👩‍👧 輸入／修改緊急聯絡人電話與LINE", uri=edit_ice_uri)),
        ButtonComponent(style="secondary", height="sm", action=URIAction(label="✏️ 輸入／修改醫療資料", uri=edit_med_uri)),
        ButtonComponent(style="secondary", height="sm", action=URIAction(label="🏠 登記／修改居住地址", uri=edit_addr_uri)),
        ButtonComponent(style="secondary", height="sm", action=URIAction(label="📱 登記／修改本人電話（供警報通報）", uri=edit_phone_uri)),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="📋 返回我的守護狀態", text="我的狀態"))
    ])

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
            BoxComponent(layout="vertical", background_color="#F0F7FF", corner_radius="8px", padding_all="12px", contents=[
                TextComponent(text="👨‍👩‍👧 緊急聯絡人（姓名／電話／LINE）：", size="xs", weight="bold", color="#007AFF"),
                TextComponent(text=ice_contact, size="sm", color="#222222", margin="sm", wrap=True)
            ]),
            TextComponent(text=f"📍 登記居住地址：{loc_str}", size="xs", color="#333333", wrap=True),
            TextComponent(
                text="💡 範例格式：緊急聯絡人:兒子王大明 0912345678 LINE:ming123（填妥電話後，上方會自動出現綠色一鍵直撥按鈕！）",
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
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="👥 管理／解綁緊急聯絡人", text="管理聯絡人")),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="🪪 緊急醫療卡／聯絡人與地址", text="醫療卡")),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="💊 設定每日用藥提醒", text="用藥提醒")),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="🛡️ 防詐騙快篩（貼上可疑簡訊）", text="防詐騙")),
        ButtonComponent(style="secondary", height="sm", action=MessageAction(label="✈️ 外出／住院暫停守護", text="暫停模式"))
    ]
    if lat is not None and lon is not None:
        enc_addr = urllib.parse.quote(addr)
        map_uri = f"{RENDER_BASE_URL}/care_map?lat={lat}&lon={lon}&addr={enc_addr}&openExternalBrowser=1"
        footer_btns.append(ButtonComponent(style="primary", color="#FF3B30", height="sm", action=URIAction(label="🏥 最後位置與周邊藥局／急診", uri=map_uri)))
    footer_btns.append(ButtonComponent(style="secondary", height="sm", action=URIAction(label="📍 傳送 GPS 定位座標", uri=loc_uri)))
    footer_btns.append(ButtonComponent(style="secondary", height="sm", action=MessageAction(label="🗑️ 刪除個人資料（完全重置）", text="刪除個人資料")))

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
        f"🕒 首次簽到時間：{t_str}\n⏰ 目前守護週期：24 小時（逾時先提醒本人，2 小時後通知聯絡人）\n\n💡 請先填寫「緊急醫療卡／聯絡人與地址」並傳送 GPS 定位，最多可邀請 5 位家人成為緊急聯絡人！",
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

    if txt in ["防詐騙", "詐騙", "查詐騙", "防詐"]:
        S = chr(47)
        check_uri = f"https:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{urllib.parse.quote('查詐騙:')}"
        scam_guide = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#FF9500", padding_all="14px", contents=[
                TextComponent(text="🛡️ 居家防詐騙快篩助手", weight="bold", size="md", color="#FFFFFF", wrap=True)
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                TextComponent(text="收到奇怪的簡訊、不明網址或投資群組邀請嗎？先別急著點連結或匯款！", size="sm", weight="bold", color="#111111", wrap=True),
                BoxComponent(layout="vertical", background_color="#FFF9EB", corner_radius="8px", padding_all="12px", contents=[
                    TextComponent(text="🔍 怎麼幫您檢查？\n1. 點擊下方橘色按鈕（輸入框會自動帶入「查詐騙:」）。\n2. 把收到的可疑簡訊或網址直接貼上送出。\n3. 系統會立刻幫您分析詐騙風險特徵！", size="xs", color="#333333", wrap=True)
                ])
            ]),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=[
                ButtonComponent(style="primary", color="#FF9500", height="sm", action=URIAction(label="🔍 貼上可疑簡訊／網址立即檢查", uri=check_uri)),
                ButtonComponent(style="primary", color="#FF3B30", height="sm", action=URIAction(label="📞 直接撥打 165 反詐騙專線", uri="tel:165")),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="📋 返回我的守護狀態", text="我的狀態"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="居家防詐騙快篩助手", contents=scam_guide))
        return

    if txt.startswith("查詐騙:") or txt.startswith("查詐騙："):
        content = txt[4:].strip()
        if not content:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 請在「查詐騙:」後面貼上您收到的簡訊內容或網址喔！"))
            return

        high_risk_keywords = [
            "飆股", "保證獲利", "老師帶隊", "解除分期", "操作ATM", "網銀", "驗證碼", 
            "水費逾期", "電費逾期", "罰單未繳", "通行費", "eTag", "包裹無法投遞", 
            "普發現金", "點數到期", "帳戶異常", "凍結", "匯款", "USDT", "泰達幣", "加賴領取"
        ]
        suspicious_domains = [".xyz", ".top", ".vip", ".icu", ".club", ".cc", "bit.ly", "tinyurl", "reurl.cc", "cutt.ly"]

        matched_reasons = []
        for kw in high_risk_keywords:
            if kw in content:
                matched_reasons.append(f"• 含有常見詐騙關鍵字「{kw}」")
        if "http://" in content.lower() or "https://" in content.lower():
            matched_reasons.append("• 訊息內夾帶外部網址連結（切勿隨意點擊輸入個資）")
        for dom in suspicious_domains:
            if dom in content.lower():
                matched_reasons.append(f"• 含有高風險短網址或可疑網域（{dom}）")

        if len(matched_reasons) >= 1:
            header_bg = "#FF3B30"
            risk_title = "🚨 高度疑似詐騙訊息！請勿點擊或匯款"
            risk_desc = "系統在您貼上的內容中偵測到以下高風險特徵：\n" + "\n".join(matched_reasons[:4]) + "\n\n⚠️ 提醒：政府機關、水電公司與銀行絕不會用簡訊網址要求您輸入帳號密碼或操作轉帳！"
        else:
            header_bg = "#1DB446"
            risk_title = "🟡 未發現明顯關鍵字，但仍請保持警覺"
            risk_desc = "雖然未偵測到典型釣魚關鍵字，但新型詐騙手法層出不窮：\n• 凡是談到「錢、匯款、投資、提供驗證碼」一律先掛斷查證。\n• 建議直接撥打下方 165 反詐騙專線或詢問家人確認！"

        result_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color=header_bg, padding_all="14px", contents=[
                TextComponent(text=risk_title, weight="bold", size="md", color="#FFFFFF", wrap=True)
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                BoxComponent(layout="vertical", background_color="#F5F7FA", corner_radius="8px", padding_all="12px", contents=[
                    TextComponent(text=risk_desc, size="xs", color="#222222", wrap=True)
                ])
            ]),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=[
                ButtonComponent(style="primary", color="#FF3B30", height="sm", action=URIAction(label="📞 撥打 165 反詐騙專線查證", uri="tel:165")),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="📋 返回我的守護狀態", text="我的狀態"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="防詐騙快篩檢測結果", contents=result_card))
        return
    if txt in ["醫療卡", "緊急醫療卡", "急救卡"]:
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        med = st.get("medical_info", "尚未填寫")
        loc = st.get("last_location", "尚未設定")
        phone = st.get("phone", "尚未設定")
        ice = st.get("ice_contact", "尚未設定")
        m_card = make_medical_card(uname, med, loc, phone, ice)
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
        ice = st.get("ice_contact", "尚未設定")
        m_card = make_medical_card(uname, med_content, loc, phone, ice)
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="緊急醫療卡更新成功", contents=m_card))
        return

    if txt.startswith("緊急聯絡人:") or txt.startswith("緊急聯絡人："):
        ice_val = txt[6:].strip()
        if not ice_val:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 請在「緊急聯絡人:」後面輸入聯絡人姓名、電話與 LINE ID 喔！\n例如：緊急聯絡人:兒子王大明 0912345678 LINE:ming123"))
            return
        call_sheet({"action": "set_ice_contact", "user_id": uid, "user_name": uname, "ice_contact": ice_val})
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        med = st.get("medical_info", "尚未填寫")
        loc = st.get("last_location", "尚未設定")
        phone = st.get("phone", "尚未設定")
        m_card = make_medical_card(uname, med, loc, phone, ice_val)
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="緊急聯絡人資料更新成功", contents=m_card))
        return

    if txt.startswith("地址:") or txt.startswith("地址："):
        addr_val = txt[3:].strip()
        if not addr_val:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 請在「地址:」後面輸入您的完整居住地址與樓層喔！\n例如：地址:新北市五股區成泰路一段100號5樓"))
            return
        res = call_sheet({"action": "set_address", "user_id": uid, "user_name": uname, "address": addr_val})
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        med = st.get("medical_info", "尚未填寫")
        loc = res.get("last_location", addr_val)
        phone = st.get("phone", "尚未設定")
        ice = st.get("ice_contact", "尚未設定")
        m_card = make_medical_card(uname, med, loc, phone, ice)
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="居住地址登記成功", contents=m_card))
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
        ice = st.get("ice_contact", "尚未設定")
        m_card = make_medical_card(uname, med, loc, phone_val, ice)
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

    if txt in ["暫停模式", "暫停守護", "外出暫停", "暫停"]:
        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        g_st = st.get("guard_status", "NORMAL")
        if g_st == "PAUSED_INDEFINITE":
            cur_mode_txt = "⏸️ 目前狀態：長期暫停中（需手動啟動）"
        elif g_st.startswith("PAUSED_"):
            cur_mode_txt = f"⏸️ 目前狀態：暫停中（將於 {g_st.replace('PAUSED_', '')} 自動恢復）"
        else:
            cur_mode_txt = "🟢 目前狀態：日常平安守護啟動中"

        pause_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#5856D6", padding_all="14px", contents=[
                TextComponent(text="✈️ 外出／住院暫停與啟動管理", weight="bold", size="md", color="#FFFFFF", wrap=True)
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                TextComponent(text=cur_mode_txt, size="sm", weight="bold", color="#5856D6", wrap=True),
                TextComponent(text="出國旅遊、住院檢查或與家人同住期間，可選擇暫停天數或長期暫停；返家後點擊「▶️ 立即啟動守護」即可恢復計時：", size="xs", color="#555555", wrap=True),
                BoxComponent(layout="horizontal", spacing="sm", contents=[
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label="⏸️ 暫停3天", text="暫停3天")),
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label="⏸️ 暫停7天", text="暫停7天"))
                ])
            ]),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=[
                ButtonComponent(style="primary", color="#FF9500", height="sm", action=MessageAction(label="⏸️ 長期暫停（直到手動啟動）", text="長期暫停")),
                ButtonComponent(style="primary", color="#1DB446", height="sm", action=MessageAction(label="▶️ 立即啟動日常守護", text="啟動守護")),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="📋 返回我的守護狀態", text="我的狀態"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="外出／住院暫停與啟動管理", contents=pause_card))
        return

    if txt in ["暫停3天", "暫停7天", "長期暫停"]:
        mode_val = "indefinite" if txt == "長期暫停" else ("3" if "3" in txt else "7")
        res = call_sheet({"action": "pause", "user_id": uid, "user_name": uname, "mode": mode_val})
        r_time = res.get("resume_time", "")
        title_label = "⏸️ 已開啟長期暫停守護" if mode_val == "indefinite" else f"⏸️ 已暫停守護（{mode_val} 天）"
        card = make_main_card(
            title_label,
            f"{uname}，已為您暫停失聯通報功能！",
            f"🕒 恢復規則：{r_time}\n🛡️ 期間內系統不會發送失聯警報；若想提前恢復，只要點擊「▶️ 啟動守護」或「🟢 我今天很好」即可立刻重新啟動！",
            uid
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=title_label, contents=card))
        return

    if txt in ["啟動守護", "恢復守護", "啟動"]:
        res = call_sheet({"action": "checkin", "user_id": uid, "user_name": uname})
        t_str = res.get("time", "剛剛")
        cur_h = res.get("alert_hours", 24)
        card = make_main_card(
            "▶️ 日常平安守護已正式啟動！",
            f"{uname}，歡迎回來！系統已重新開始為您守護：",
            f"🕒 啟動時間：{t_str}\n⏰ 守護週期：{cur_h} 小時（含 2 小時緩衝）\n🟢 守護狀態：正常運作中",
            uid
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="日常平安守護已正式啟動", contents=card))
        return
    if txt in ["管理聯絡人", "聯絡人清單", "解綁聯絡人", "聯絡人"]:
        S = chr(47)
        bind_cmd = urllib.parse.quote(f"綁定:{uid}")
        share_text = urllib.parse.quote(f"我正在使用「獨居平安簽到助手」每天報平安！請點擊下方連結成為我的緊急聯絡人（最多可綁定 5 位親友）：\nhttps:{S}{S}line.me{S}R{S}oaMessage{S}{LINE_BOT_BASIC_ID}{S}?{bind_cmd}")
        share_uri = f"https:{S}{S}line.me{S}R{S}share?text={share_text}"

        st = call_sheet({"action": "status", "user_id": uid, "user_name": uname})
        c_ids = st.get("contact_ids", [])

        body_contents = [
            TextComponent(text=f"目前守護人數：{len(c_ids)} / 5 位", size="sm", weight="bold", color="#111111"),
            TextComponent(text="失聯警報觸發時，系統會同步通知以下所有聯絡人。如需解除某位聯絡人，請直接點擊下方對應的移除按鈕：", size="xs", color="#555555", wrap=True)
        ]
        footer_btns = []

        if len(c_ids) == 0:
            body_contents.append(TextComponent(text="（目前尚未綁定任何緊急聯絡人）", size="xs", color="#888888", margin="md"))
        else:
            for idx, cid in enumerate(c_ids, 1):
                c_name = get_user_name(cid)
                display_c = c_name if c_name != "用戶" else f"聯絡人 #{idx} ({cid[:6]}...)"
                footer_btns.append(
                    ButtonComponent(style="secondary", height="sm", action=MessageAction(label=f"❌ 移除：{display_c}", text=f"解綁:{cid}"))
                )
            footer_btns.append(
                ButtonComponent(style="primary", color="#FF3B30", height="sm", action=MessageAction(label="🗑️ 一鍵清空全部聯絡人", text="清空聯絡人"))
            )

        if len(c_ids) < 5:
            footer_btns.append(
                ButtonComponent(style="primary", color="#007AFF", height="sm", action=URIAction(label="➕ 繼續邀請家人（上限5位）", uri=share_uri))
            )
        footer_btns.append(
            ButtonComponent(style="secondary", height="sm", action=MessageAction(label="📋 返回我的守護狀態", text="我的狀態"))
        )

        manage_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#007AFF", padding_all="14px", contents=[
                TextComponent(text="👥 緊急聯絡人管理與解綁", weight="bold", size="md", color="#FFFFFF", wrap=True)
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=body_contents),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=footer_btns)
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="緊急聯絡人管理與解綁", contents=manage_card))
        return

    if txt.startswith("解綁:"):
        target_cid = txt[3:].strip()
        res = call_sheet({"action": "remove_contact", "user_id": uid, "contact_id": target_cid})
        rem_cnt = res.get("count", 0)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已成功解除該位緊急聯絡人的綁定！目前剩餘 {rem_cnt} / 5 位守護聯絡人。\n（輸入「管理聯絡人」可隨時查看最新名單）"))
        return

    if txt in ["清空聯絡人", "全部解綁"]:
        call_sheet({"action": "clear_contacts", "user_id": uid})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🗑️ 已為您清空所有緊急聯絡人名單！如需重新設定，請點擊主卡片上的「👨‍👩‍👧 邀請家人」按鈕。"))
        return

    if txt in ["刪除個人資料", "刪除資料", "清除個人資料", "註銷帳號"]:
        del_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#FF3B30", padding_all="14px", contents=[
                TextComponent(text="⚠️ 確認要徹底刪除個人資料？", weight="bold", size="md", color="#FFFFFF", wrap=True)
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                TextComponent(text="執行後，系統將從雲端資料庫永久刪除您的所有紀錄，且無法復原：", size="sm", weight="bold", color="#111111", wrap=True),
                TextComponent(text="• 簽到時間與守護週期設定\n• 已綁定的緊急聯絡人名單\n• 登記居住地址、電話、緊急醫療卡與用藥提醒", size="xs", color="#555555", wrap=True),
                TextComponent(text="（若未來想重新使用，只需再次點擊「我今天很好」即可重新建檔）", size="xxs", color="#888888", wrap=True)
            ]),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=[
                ButtonComponent(style="primary", color="#FF3B30", height="sm", action=MessageAction(label="🚨 確定永久刪除我的所有資料", text="確認刪除個人資料")),
                ButtonComponent(style="secondary", height="sm", action=MessageAction(label="↩️ 取消，返回我的守護狀態", text="我的狀態"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="確認刪除個人資料", contents=del_card))
        return

    if txt == "確認刪除個人資料":
        call_sheet({"action": "delete_user", "user_id": uid})
        bye_card = BubbleContainer(
            size="mega",
            header=BoxComponent(layout="vertical", background_color="#333333", padding_all="14px", contents=[
                TextComponent(text="🗑️ 個人資料已徹底刪除", weight="bold", size="md", color="#FFFFFF")
            ]),
            body=BoxComponent(layout="vertical", spacing="md", contents=[
                TextComponent(text="您的所有平安簽到紀錄、聯絡人綁定、地址座標、電話與緊急醫療卡資訊均已從資料庫永久移除，系統也不會再對您發送任何推播通知。", size="xs", color="#333333", wrap=True),
                TextComponent(text="感謝您的使用！若日後有需要，隨時點擊下方按鈕即可重新建檔啟用。", size="xs", color="#666666", wrap=True)
            ]),
            footer=BoxComponent(layout="vertical", contents=[
                ButtonComponent(style="primary", color="#1DB446", height="sm", action=MessageAction(label="🟢 重新啟用並報平安", text="我今天很好"))
            ])
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="個人資料已徹底刪除", contents=bye_card))
        return
    if txt in ["教學", "說明", "使用教學", "怎麼用", "幫助", "?"]:
        guide_card = make_main_card(
            "📖 獨居平安簽到助手 · 使用教學",
            "每天花 1 秒鐘報平安，守護您的居家安全：",
            "1. 每日簽到：點綠色按鈕重新計算守護時間。\n2. 緊急醫療卡與聯絡人直撥：登記血型病史、門牌地址、本人電話與緊急聯絡人電話/LINE，隨時可一鍵直撥緊急聯絡人或 119。\n3. 用藥提醒與防詐騙：支援多組「小時:分鐘」吃藥提醒，以及可疑簡訊一鍵快篩。\n4. 外出暫停與帳號自主權：支援 3天/7天/長期暫停，最多綁定 5 位家人並可隨時一鍵解綁或刪除個人資料！",
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
        g_st = res.get("guard_status", "NORMAL")
        if g_st == "PAUSED_INDEFINITE":
            guard_label = "⏸️ 長期暫停中（需手動啟動）"
        elif g_st.startswith("PAUSED_"):
            guard_label = f"⏸️ 暫停中（{g_st.replace('PAUSED_', '')} 恢復）"
        else:
            guard_label = "🟢 正常啟動中"

        loc_str = res.get("last_location", "尚未設定")
        med_str = res.get("medical_info", "尚未填寫")
        med_t = res.get("med_times", "尚未設定")
        phone_str = res.get("phone", "尚未設定")
        ice_str = res.get("ice_contact", "尚未設定")
        lat, lon = parse_latlon(loc_str)
        clean_addr = next(iter(loc_str.split("("))).strip() if "(" in loc_str else "最後平安位置"
        card = make_main_card(
            "📋 我的平安守護狀態",
            f"守護對象：{uname}",
            f"🛡️ 守護開關：{guard_label}\n🕒 最後簽到：{res.get('last_checkin', '無紀錄')}\n⏰ 通報週期：{cur_h} 小時（含 2 小時緩衝）\n💊 用藥提醒：{med_t}\n👨‍👩‍👧 聯絡人狀態：{c_status}\n📞 本人電話：{phone_str}\n🆘 醫療卡聯絡人：{ice_str}\n🪪 緊急醫療卡：{med_str}\n📍 最後平安位置：{loc_str}",
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
    clean_addr = next(iter(loc_str.split("("))).strip() if "(" in loc_str else "最後平安位置"
    card = make_main_card(
        "✅ 今日平安簽到成功！",
        f"{uname}，很高興知道您今天一切安好！",
        f"🕒 簽到時間：{t_str}\n⏰ 守護週期：{cur_h} 小時（含 2 小時緩衝）\n🔔 守護狀態：{c_note}",
        uid, lat=lat, lon=lon, addr=clean_addr
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="今日平安簽到成功", contents=card))
