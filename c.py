import asyncio
import os
import json
import base64
import subprocess
from aiohttp import web
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- MONKEY PATCH FOR PYROGRAM/PYTGCALLS ---
import pyrogram.errors
if not hasattr(pyrogram.errors, 'GroupcallForbidden'):
    class GroupcallForbidden(Exception): pass
    pyrogram.errors.GroupcallForbidden = GroupcallForbidden

from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

# --- CONFIGURATION (via getenv) ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
VPS_PUBLIC_IP = os.getenv("VPS_IP")
WEB_PORT = 5000

# Safety Check
if not all([TWILIO_SID, TWILIO_TOKEN, BOT_TOKEN]):
    print("CRITICAL ERROR: Credentials not found in .env file!")
    exit(1)

# --- INITIALIZATION ---
bot = Client("telephony_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
call_app = PyTgCalls(user)
twilio_api = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
active_sessions = {}

# --- WEB SERVER & HANDLERS ---
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

@bot.on_message(filters.command("call") & filters.group)
async def call_handler(client, message):
    if len(message.command) < 2:
        return await message.reply("Invalid syntax. Use: /call +91...")
    
    target = message.command[1]
    chat_id = message.chat.id
    status_msg = await message.reply("Initiating encrypted call...")

    try:
        outbound = twilio_api.calls.create(
            url=f"http://{VPS_PUBLIC_IP}:{WEB_PORT}/voice?cid={chat_id}",
            to=target, from_=TWILIO_NUMBER
        )
        active_sessions[chat_id] = {
            "sid": outbound.sid, "is_recording": False, "record_handle": None,
            "raw_path": f"/tmp/rec_{chat_id}.raw", "final_path": f"/tmp/final_{chat_id}.ogg"
        }
        
        fifo_path = f"/tmp/twilio_{chat_id}.fifo"
        if not os.path.exists(fifo_path): os.mkfifo(fifo_path)
        await call_app.play(chat_id, MediaStream(fifo_path))
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("End Call", callback_data="end"),
             InlineKeyboardButton("Join VC", url=f"https://t.me/{message.chat.username}?videochat")],
            [InlineKeyboardButton("Start Recording", callback_data="rec")]
        ])
        await status_msg.edit(f"Call active to {target}\nAudio bridged to Voice Chat.", reply_markup=kb)
    except Exception as e:
        await status_msg.edit(f"Twilio Error: {e}")

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
        await query.message.edit_text("Call Connection Closed.")
    elif query.data == "rec" and session:
        session["is_recording"] = True
        session["record_handle"] = open(session["raw_path"], "wb")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("End Call", callback_data="end"),
             InlineKeyboardButton("Join VC", url=f"https://t.me/{query.message.chat.username}?videochat")],
            [InlineKeyboardButton("Stop Recording", callback_data="stop_rec")]
        ])
        await query.message.edit_reply_markup(reply_markup=kb)
        await query.answer("Recording started.")
    elif query.data == "stop_rec" and session:
        session["is_recording"] = False
        session["record_handle"].close()
        session["record_handle"] = None
        subprocess.run(["ffmpeg", "-y", "-f", "mulaw", "-ar", "8000", "-i", session['raw_path'], "-c:a", "libopus", session['final_path']])
        await client.send_voice(cid, session["final_path"], caption="Call Log Transmission")

async def main():
    app = web.Application()
    app.add_routes([web.post('/voice', voice_webhook), web.get('/stream/{cid}', websocket_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', WEB_PORT).start()
    await bot.start()
    await user.start()
    async for d in user.get_dialogs(): pass
    await call_app.start()
    print("--- SYSTEM ONLINE ---")
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
    
