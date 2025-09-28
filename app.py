import os
import logging
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI  # OpenAI 官方 SDK v1.x

# -------------------- 基本設定 --------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 必填環境變數（LINE）
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise RuntimeError("缺少 LINE 環境變數：LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# 模型可用環境變數控制（預設 gpt-4o，失敗退 gpt-4o-mini）
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o").strip()
OPENAI_FALLBACK_MODEL = os.environ.get("OPENAI_FALLBACK_MODEL", "gpt-4o-mini").strip()

# -------------------- 讀取 System Prompt（支援分段） --------------------
def load_system_prompt() -> str:
    txt = (os.environ.get("SYSTEM_PROMPT", "") or "").strip()
    if txt:
        return txt
    parts = []
    i = 1
    while True:
        seg = (os.environ.get(f"SYSTEM_PROMPT_P{i}", "") or "").strip()
        if not seg:
            break
        parts.append(seg)
        i += 1
    if parts:
        return "\n".join(parts)
    # 後備：若未設定，給極簡預設
    return "你是一位溫柔且堅定的中文陪聊者。回覆要短、同理、具體；不提供醫療診斷。"

SYSTEM_PROMPT = load_system_prompt()

# -------------------- 危機偵測 --------------------
CRISIS_WORDS = ["自殺", "想死", "不想活", "輕生", "自我了斷", "割腕", "跳樓", "傷害自己"]
CRISIS_REPLY = (
    "⚠️ 我很在乎你的安全，也謝謝你願意說出來。\n"
    "若你有立即危險，請立刻撥打 110 / 119。\n"
    "需要傾訴可撥 **1925 安心專線**（24小時）。\n"
    "如果你願意，也可以告訴我：此刻最讓你難受的是什麼？我會在這裡陪你。"
)

# -------------------- 健康檢查與首頁 --------------------
@app.get("/health")
def health():
    return "ok", 200

@app.get("/")
def index():
    return (
        "<h3>LINE bot is running.</h3>"
        "<p>/health → ok ・ /callback ← LINE Webhook</p>",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )

# -------------------- 呼叫 OpenAI --------------------
def ask_gpt(user_text: str) -> str:
    if not client:
        return f"（尚未設定 OPENAI_API_KEY）你剛剛說：{user_text}"

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            temperature=0.7,
            max_tokens=400,
            timeout=20,  # 逾時保護
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        app.logger.error(f"[openai primary {OPENAI_MODEL}] {e}; fallback to {OPENAI_FALLBACK_MODEL}")
        try:
            resp = client.chat.completions.create(
                model=OPENAI_FALLBACK_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.7,
                max_tokens=400,
                timeout=20,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e2:
            app.logger.error(f"[openai fallback {OPENAI_FALLBACK_MODEL}] {e2}")
            return "抱歉，我這邊暫時遇到問題，但我仍在這裡。願意多說一點發生了什麼嗎？"

# -------------------- LINE Webhook --------------------
@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    app.logger.info(f"[callback] body_len={len(body)}")

    if not signature:
        app.logger.error("[callback] Missing X-Line-Signature")
        return "missing signature", 400

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("[callback] Invalid signature（請檢查 LINE_CHANNEL_SECRET）")
        return "invalid signature", 400

    return "OK", 200

# -------------------- 文字訊息處理 --------------------
@handler.add(MessageEvent, message=TextMessage)
def on_text(event: MessageEvent):
    user_text = (event.message.text or "").strip()

    # 危機優先
    if any(w in user_text for w in CRISIS_WORDS):
        reply = TextSendMessage(text=CRISIS_REPLY)
    else:
        reply_text = ask_gpt(user_text)
        reply = TextSendMessage(text=reply_text)

    try:
        line_bot_api.reply_message(event.reply_token, reply)
    except Exception as e:
        app.logger.error(f"[line reply] {e}")

# -------------------- 本機啟動（雲端用 gunicorn） --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
