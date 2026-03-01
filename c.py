import asyncio
import os
import json
import base64
import subprocess
import logging
from aiohttp import web

from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

logging.basicConfig(level=logging.INFO)

# ---------------- CONFIG ----------------
API_ID = 21705136
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8750484092:AAGWLBGJgFXYG65iJf4Mm_nh0tQ4C8q1IEc"
SESSION_STRING = "BQFLMbAANQBC6oPztCBRPNiCK1HU-eNwJj4rBtJf5gTWezHVVmATq8DeaGvhvT4v4bVyezTHryiiFy7gJHum2SJH9N181w7WZJyhuXEunRTpHPf4kdJinTxl02XAV43hpYTowjAArdyJYrwXRrakYU-ouC4KvEX5nt0VI9pbTZAWlClv-6hj0Cx2JPrvy63sQ-OCTrAVFCWVjfjkLXvhk433oxGJpXxY-a8F0wB0TUSI29SfpA3ShIdvZCJ4KHTsAjLnMzsEHJhX8GphD-H_s5QW4z_JjgvY8eOkwAYk7ZB_AkSbTTqf7pfrl8_FXXKzIfxpsVvLbS8d8t62uZ9pcJCIODnskgAAAAGd7PcCAA"

TWILIO_TOKEN = "" 
TWILIO_NUMBER = "+14482173794"
VPS_PUBLIC_IP = "16.171.30.40" 
WEB_PORT = 5000

# ----------- CHECK CREDS -------------
if not TWILIO_SID or not TWILIO_TOKEN:
    raise Exception("Twilio credentials missing")

# ------------ INIT -------------------
bot = Client("telephony_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
call_app = PyTgCalls(user)
twilio_api = TwilioClient(TWILIO_SID, TWILIO_TOKEN)

active_sessions = {}

# -------- WEBHOOK --------
async def voice_webhook(request):
    cid = request.query.get('cid', '0')
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f'wss://{VPS_PUBLIC_IP}:{WEB_PORT}/stream/{cid}')
    response.append(connect)
    return web.Response(text=str(response), content_type='text/xml')

async def websocket_handler(request):
    cid = int(request.match_info.get('cid', 0))
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    fifo = f"/tmp/twilio_{cid}.fifo"
    if not os.path.exists(fifo):
        os.mkfifo(fifo)

    try:
        with open(fifo, "wb") as f:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("event") == "media":
                        f.write(base64.b64decode(data["media"]["payload"]))
                        f.flush()
    except Exception as e:
        logging.error(e)

    return ws

# ---------- BOT COMMANDS -----------

@bot.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("✅ Telephony Bot Running")

@bot.on_message(filters.command("call"))
async def call_handler(client, message):

    if len(message.command) < 2:
        return await message.reply("Use: /call +91xxxxxxxxxx")

    number = message.command[1]
    cid = message.chat.id

    await message.reply("📞 Calling...")

    try:
        outbound = twilio_api.calls.create(
            url=f"http://{VPS_PUBLIC_IP}:{WEB_PORT}/voice?cid={cid}",
            to=number,
            from_=TWILIO_NUMBER
        )

        active_sessions[cid] = outbound.sid

        await message.reply(f"✅ Calling {number}")

    except Exception as e:
        await message.reply(f"❌ Error:\n{e}")

@bot.on_callback_query()
async def cb(client, q: CallbackQuery):
    await q.answer("OK")

# ----------- MAIN -----------

async def main():

    app = web.Application()
    app.add_routes([
        web.post('/voice', voice_webhook),
        web.get('/stream/{cid}', websocket_handler)
    ])

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEB_PORT).start()

    await bot.start()
    await user.start()
    await call_app.start()

    print("✅ SYSTEM ONLINE")

    await idle()

if __name__ == "__main__":
    asyncio.run(main())
