import asyncio
from dotenv import load_dotenv
import os
import json
import base64
import subprocess
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- CRITICAL FIX: PYROGRAM/PYTGCALLS MONKEY PATCH ---
import pyrogram.errors
if not hasattr(pyrogram.errors, 'GroupcallForbidden'):
    class GroupcallForbidden(Exception): pass
    pyrogram.errors.GroupcallForbidden = GroupcallForbidden
# ----------------------------------------------------

from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

# --- CONFIGURATION ---
API_ID = 21705136
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8750484092:AAGWLBGJgFXYG65iJf4Mm_nh0tQ4C8q1IEc"
SESSION_STRING = "BQFLMbAANQBC6oPztCBRPNiCK1HU-eNwJj4rBtJf5gTWezHVVmATq8DeaGvhvT4v4bVyezTHryiiFy7gJHum2SJH9N181w7WZJyhuXEunRTpHPf4kdJinTxl02XAV43hpYTowjAArdyJYrwXRrakYU-ouC4KvEX5nt0VI9pbTZAWlClv-6hj0Cx2JPrvy63sQ-OCTrAVFCWVjfjkLXvhk433oxGJpXxY-a8F0wB0TUSI29SfpA3ShIdvZCJ4KHTsAjLnMzsEHJhX8GphD-H_s5QW4z_JjgvY8eOkwAYk7ZB_AkSbTTqf7pfrl8_FXXKzIfxpsVvLbS8d8t62uZ9pcJCIODnskgAAAAGd7PcCAA"

TWILIO_SID = os.getenv("SID")
TWILIO_TOKEN = os.getenv("AUTH")
TWILIO_NUMBER = "+14482173794"
VPS_PUBLIC_IP = "16.171.30.40" 
WEB_PORT = 5000

# --- INITIALIZATION ---
bot = Client("telephony_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
call_app = PyTgCalls(user)
twilio_api = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
active_sessions = {}

# --- WEB SERVER ---
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
    except: pass
    return ws

# --- HANDLERS ---
@bot.on_message(filters.command("start"))
async def start_handler(client, message):
    print(f"DEBUG: Start command received in {message.chat.id}") # Terminal logging
    await message.reply("Telephony System is Online. Use /call [number] in group.")

@bot.on_message(filters.command("call") & filters.group)
async def call_handler(client, message):
    print(f"DEBUG: Call command received for {message.command}") # Terminal logging
    if len(message.command) < 2:
        return await message.reply("Usage: /call +919509203839")
    
    target = message.command[1]
    chat_id = message.chat.id
    fifo_path = f"/tmp/twilio_{chat_id}.fifo"
    
    if not os.path.exists(fifo_path): os.mkfifo(fifo_path)
    status_msg = await message.reply("Establishing connection...")

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
        await status_msg.edit(f"Call active to {target}.\nAudio bridged to Voice Chat.", reply_markup=kb)
    except Exception as e:
        await status_msg.edit(f"Telephony Error: {e}")

@bot.on_callback_query()
async def actions(client, query: CallbackQuery):
    cid = query.message.chat.id
    session = active_sessions.get(cid)
    
    if query.data == "end":
        if session:
            try: twilio_api.calls(session["sid"]).update(status="completed")
            except: pass
            if session["record_handle"]: session["record_handle"].close()
            del active_sessions[cid]
        await call_app.leave_call(cid)
        await query.message.edit_text("Call Disconnected.")
    
    elif query.data == "rec" and session:
        session["is_recording"] = True
        session["record_handle"] = open(session["raw_path"], "wb")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("End Call", callback_data="end"),
             InlineKeyboardButton("Join VC", url=f"https://t.me/{query.message.chat.username}?videochat")],
            [InlineKeyboardButton("Stop Recording", callback_data="stop_rec")]
        ])
        await query.message.edit_reply_markup(reply_markup=kb)
        await query.answer("System Recording...")
        
    elif query.data == "stop_rec" and session:
        session["is_recording"] = False
        session["record_handle"].close()
        session["record_handle"] = None
        subprocess.run(["ffmpeg", "-y", "-f", "mulaw", "-ar", "8000", "-i", session['raw_path'], "-c:a", "libopus", session['final_path']])
        await client.send_voice(cid, session["final_path"], caption="Call Recording File")

# --- MAIN ---
async def main():
    # Start Server
    app = web.Application()
    app.add_routes([web.post('/voice', voice_webhook), web.get('/stream/{cid}', websocket_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', WEB_PORT).start()
    
    # Start Clients
    await bot.start()
    await user.start()
    
    # Fix Peer ID
    async for dialog in user.get_dialogs():
        pass
        
    await call_app.start()
    print("--- SYSTEM ONLINE ---")
    await idle() # Correct way to keep bot running
    
    # Cleanup on exit
    await bot.stop()
    await user.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    
