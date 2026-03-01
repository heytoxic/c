import asyncio
import os
import json
import base64
import subprocess
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from twilio.rest import Client as TwilioClient

# --- CONFIGURATION & CREDENTIALS ---
API_ID = "21705136"
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8750484092:AAGWLBGJgFXYG65iJf4Mm_nh0tQ4C8q1IEc"
SESSION_STRING = "BQFLMbAANQBC6oPztCBRPNiCK1HU-eNwJj4rBtJf5gTWezHVVmATq8DeaGvhvT4v4bVyezTHryiiFy7gJHum2SJH9N181w7WZJyhuXEunRTpHPf4kdJinTxl02XAV43hpYTowjAArdyJYrwXRrakYU-ouC4KvEX5nt0VI9pbTZAWlClv-6hj0Cx2JPrvy63sQ-OCTrAVFCWVjfjkLXvhk433oxGJpXxY-a8F0wB0TUSI29SfpA3ShIdvZCJ4KHTsAjLnMzsEHJhX8GphD-H_s5QW4z_JjgvY8eOkwAYk7ZB_AkSbTTqf7pfrl8_FXXKzIfxpsVvLbS8d8t62uZ9pcJCIODnskgAAAAGd7PcCAA"

TWILIO_SID = "AC6134464586bae7fa19b92a350c6708a9"
TWILIO_TOKEN = "42f5c815ecb92643d45d18a52f1a8440"
TWILIO_NUMBER = "+14482173794"

VPS_PUBLIC_IP = "YOUR_VPS_PUBLIC_IP" # CRITICAL: Insert your Ubuntu VPS IP here
WEB_PORT = 5000

# --- INITIALIZATION ---
bot = Client("telephony_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
call_app = PyTgCalls(user)
twilio_api = TwilioClient(TWILIO_SID, TWILIO_TOKEN)

active_sessions = {}

# --- AIOHTTP WEBHOOK & WEBSOCKET SERVER ---
async def voice_webhook(request):
    """Instructs Twilio to open a WebSocket stream for live audio."""
    cid = request.query.get('cid', '0')
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="wss://{VPS_PUBLIC_IP}:{WEB_PORT}/stream/{cid}" />
        </Connect>
    </Response>"""
    return web.Response(text=twiml, content_type='text/xml')

async def websocket_handler(request):
    """Receives and routes the live audio payload from Twilio."""
    cid = int(request.match_info.get('cid', 0))
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    fifo_path = f"/tmp/twilio_{cid}.fifo"
    if not os.path.exists(fifo_path):
        os.mkfifo(fifo_path)
        
    try:
        with open(fifo_path, "wb") as fifo:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data['event'] == 'media':
                        payload = base64.b64decode(data['media']['payload'])
                        
                        # Route 1: Write to live Voice Chat pipe
                        try:
                            fifo.write(payload)
                            fifo.flush()
                        except Exception:
                            pass
                        
                        # Route 2: Write to recording file if active
                        session = active_sessions.get(cid)
                        if session and session.get("is_recording") and session.get("record_handle"):
                            try:
                                session["record_handle"].write(payload)
                            except Exception:
                                pass
    except Exception as e:
        print(f"Stream terminated: {e}")
        
    return ws

# --- TELEGRAM BOT LOGIC & UI ---
@bot.on_message(filters.command("call") & filters.group)
async def initiate_call(client, message):
    if len(message.command) < 2:
        await message.reply("Invalid syntax. Usage: /call +919509203839")
        return

    target = message.command[1]
    chat_id = message.chat.id
    fifo_path = f"/tmp/twilio_{chat_id}.fifo"
    raw_record_path = f"/tmp/record_{chat_id}.ulaw"
    
    if not os.path.exists(fifo_path):
        os.mkfifo(fifo_path)
    
    try:
        # Initiate outbound telecom call
        outbound_call = twilio_api.calls.create(
            url=f"http://{VPS_PUBLIC_IP}:{WEB_PORT}/voice?cid={chat_id}",
            to=target,
            from_=TWILIO_NUMBER
        )
        
        active_sessions[chat_id] = {
            "sid": outbound_call.sid, 
            "is_recording": False,
            "record_handle": None,
            "raw_path": raw_record_path,
            "final_path": f"/tmp/final_{chat_id}.ogg"
        }
        
        # Bridge the local FIFO pipe into the Telegram Voice Chat
        await call_app.play(chat_id, MediaStream(fifo_path))

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("End Call", callback_data="cmd_end"),
                InlineKeyboardButton("Join Voice Chat", url=f"https://t.me/{message.chat.username}?videochat")
            ],
            [InlineKeyboardButton("Start Recording", callback_data="cmd_record")]
        ])

        await message.reply_text(
            f"Connection Established.\nTarget: {target}\nStatus: Audio bridged to Group Voice Chat.",
            reply_markup=keyboard
        )
    except Exception as e:
        await message.reply(f"System Error: {str(e)}")

@bot.on_callback_query()
async def process_callbacks(client, query: CallbackQuery):
    cid = query.message.chat.id
    action = query.data
    session = active_sessions.get(cid)

    if not session and action != "cmd_end":
        await query.answer("This session is no longer active.", show_alert=True)
        return

    if action == "cmd_end":
        if session:
            try:
                twilio_api.calls(session["sid"]).update(status="completed")
            except Exception:
                pass
            
            if session.get("record_handle"):
                session["record_handle"].close()
            
            del active_sessions[cid]
        
        try:
            await call_app.leave_call(cid)
        except Exception:
            pass

        await query.message.edit_text("Call disconnected. Session closed.")
        await query.answer("Connection terminated.")

    elif action == "cmd_record":
        session["is_recording"] = True
        session["record_handle"] = open(session["raw_path"], "wb")
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("End Call", callback_data="cmd_end"),
                InlineKeyboardButton("Join Voice Chat", url=f"https://t.me/{query.message.chat.username}?videochat")
            ],
            [InlineKeyboardButton("Stop Recording", callback_data="cmd_stop_record")]
        ])
        await query.message.edit_reply_markup(reply_markup=keyboard)
        await query.answer("System recording initiated.")

    elif action == "cmd_stop_record":
        session["is_recording"] = False
        if session["record_handle"]:
            session["record_handle"].close()
            session["record_handle"] = None
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("End Call", callback_data="cmd_end"),
                InlineKeyboardButton("Join Voice Chat", url=f"https://t.me/{query.message.chat.username}?videochat")
            ],
            [InlineKeyboardButton("Start Recording", callback_data="cmd_record")]
        ])
        await query.message.edit_reply_markup(reply_markup=keyboard)
        await query.answer("Processing recording data...")
        
        # Convert raw telecom audio to Telegram-compatible OGG format
        raw_path = session["raw_path"]
        final_path = session["final_path"]
        convert_cmd = f"ffmpeg -y -f mulaw -ar 8000 -i {raw_path} -c:a libopus {final_path}"
        subprocess.run(convert_cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Upload the converted recording back to the group
        await client.send_voice(cid, final_path, caption="Automated Call Recording")

# --- EXECUTION ---
async def main():
    # Initialize aiohttp web server concurrently
    app = web.Application()
    app.add_routes([
        web.post('/voice', voice_webhook),
        web.get('/stream/{cid}', websocket_handler)
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
    await site.start()

    # Initialize Telegram clients
    await bot.start()
    await call_app.start()
    print("System Online. Telephony, Webhook, and PyTgCalls modules are active.")
    
    import pyrogram
    await pyrogram.idle()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    
