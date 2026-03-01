import asyncio
import os
import json
import base64
import subprocess
from aiohttp import web

# --- THE MAGIC FIX (MONKEY PATCH) ---
# Ye PyTgCalls aur Pyrogram ka import error bypass karne ke liye hai
import pyrogram.errors
if not hasattr(pyrogram.errors, 'GroupcallForbidden'):
    class GroupcallForbidden(Exception):
        pass
    pyrogram.errors.GroupcallForbidden = GroupcallForbidden
# ------------------------------------

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

# --- CONFIGURATION ---
API_ID = 21705136
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8750484092:AAGWLBGJgFXYG65iJf4Mm_nh0tQ4C8q1IEc"
SESSION_STRING = "BQFLMbAANQBC6oPztCBRPNiCK1HU-eNwJj4rBtJf5gTWezHVVmATq8DeaGvhvT4v4bVyezTHryiiFy7gJHum2SJH9N181w7WZJyhuXEunRTpHPf4kdJinTxl02XAV43hpYTowjAArdyJYrwXRrakYU-ouC4KvEX5nt0VI9pbTZAWlClv-6hj0Cx2JPrvy63sQ-OCTrAVFCWVjfjkLXvhk433oxGJpXxY-a8F0wB0TUSI29SfpA3ShIdvZCJ4KHTsAjLnMzsEHJhX8GphD-H_s5QW4z_JjgvY8eOkwAYk7ZB_AkSbTTqf7pfrl8_FXXKzIfxpsVvLbS8d8t62uZ9pcJCIODnskgAAAAGd7PcCAA"

TWILIO_SID = "AC6134464586bae7fa19b92a350c6708a9"
TWILIO_TOKEN = "42f5c815ecb92643d45d18a52f1a8440"
TWILIO_NUMBER = "+14482173794"

VPS_PUBLIC_IP = "16.171.30.40" 
WEB_PORT = 5000

# --- INITIALIZATION ---
bot = Client("telephony_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# We initialize PyTgCalls after the patch and clients are defined
call_app = PyTgCalls(user)
twilio_api = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
active_sessions = {}

# --- WEB SERVER LOGIC ---
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
    fifo_path = f"/tmp/twilio_{cid}.fifo"
    if not os.path.exists(fifo_path): os.mkfifo(fifo_path)
    try:
        with open(fifo_path, "wb") as fifo:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data['event'] == 'media':
                        payload = base64.b64decode(data['media']['payload'])
                        fifo.write(payload)
                        fifo.flush()
                        session = active_sessions.get(cid)
                        if session and session["is_recording"] and session["record_handle"]:
                            session["record_handle"].write(payload)
    except Exception: pass
    return ws

# --- BOT COMMANDS ---
@bot.on_message(filters.command("call") & filters.group)
async def start_call(client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: /call +919509203839")
    
    target = message.command[1]
    chat_id = message.chat.id
    fifo_path = f"/tmp/twilio_{chat_id}.fifo"
    if not os.path.exists(fifo_path): os.mkfifo(fifo_path)

    try:
        outbound = twilio_api.calls.create(
            url=f"http://{VPS_PUBLIC_IP}:{WEB_PORT}/voice?cid={chat_id}",
            to=target, from_=TWILIO_NUMBER
        )
        active_sessions[chat_id] = {
            "sid": outbound.sid, "is_recording": False, "record_handle": None,
            "raw_path": f"/tmp/rec_{chat_id}.raw", "final_path": f"/tmp/final_{chat_id}.ogg"
        }
        await call_app.play(chat_id, MediaStream(fifo_path))
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("End Call", callback_data="end"),
             InlineKeyboardButton("Join VC", url=f"https://t.me/{message.chat.username}?videochat")],
            [InlineKeyboardButton("Start Recording", callback_data="rec")]
        ])
        await message.reply(f"Call initiated to {target}. Audio routed to Voice Chat.", reply_markup=kb)
    except Exception as e:
        await message.reply(f"Error: {e}")

@bot.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    cid = query.message.chat.id
    data = query.data
    session = active_sessions.get(cid)

    if data == "end":
        if session:
            try: twilio_api.calls(session["sid"]).update(status="completed")
            except: pass
            if session["record_handle"]: session["record_handle"].close()
            del active_sessions[cid]
        await call_app.leave_call(cid)
        await query.message.edit_text("Call Ended.")
        
    elif data == "rec":
        session["is_recording"] = True
        session["record_handle"] = open(session["raw_path"], "wb")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("End Call", callback_data="end"),
             InlineKeyboardButton("Join VC", url=f"https://t.me/{query.message.chat.username}?videochat")],
            [InlineKeyboardButton("Stop Recording", callback_data="stop_rec")]
        ])
        await query.message.edit_reply_markup(reply_markup=kb)
        await query.answer("Recording started.")

    elif data == "stop_rec":
        session["is_recording"] = False
        session["record_handle"].close()
        session["record_handle"] = None
        await query.answer("Saving recording...")
        
        # Convert raw audio
        cmd = f"ffmpeg -y -f mulaw -ar 8000 -i {session['raw_path']} -c:a libopus {session['final_path']}"
        subprocess.run(cmd.split())
        await client.send_voice(cid, session["final_path"], caption="Call Recording")

# --- MAIN ---
async def main():
    app = web.Application()
    app.add_routes([web.post('/voice', voice_webhook), web.get('/stream/{cid}', websocket_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', WEB_PORT).start()
    
    await bot.start()
    await call_app.start()
    print("System active.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
    
