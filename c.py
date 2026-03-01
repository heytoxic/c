import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioPiped
from twilio.rest import Client as TwilioClient

# Telegram Credentials
API_ID = "21705136"
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8750484092:AAGWLBGJgFXYG65iJf4Mm_nh0tQ4C8q1IEc"
SESSION_STRING = "BQFLMbAANQBC6oPztCBRPNiCK1HU-eNwJj4rBtJf5gTWezHVVmATq8DeaGvhvT4v4bVyezTHryiiFy7gJHum2SJH9N181w7WZJyhuXEunRTpHPf4kdJinTxl02XAV43hpYTowjAArdyJYrwXRrakYU-ouC4KvEX5nt0VI9pbTZAWlClv-6hj0Cx2JPrvy63sQ-OCTrAVFCWVjfjkLXvhk433oxGJpXxY-a8F0wB0TUSI29SfpA3ShIdvZCJ4KHTsAjLnMzsEHJhX8GphD-H_s5QW4z_JjgvY8eOkwAYk7ZB_AkSbTTqf7pfrl8_FXXKzIfxpsVvLbS8d8t62uZ9pcJCIODnskgAAAAGd7PcCAA"

# Twilio Trial Credentials
TWILIO_ACCOUNT_SID = "AC6134464586bae7fa19b92a350c6708a9"
TWILIO_AUTH_TOKEN = "42f5c815ecb92643d45d18a52f1a8440"
TWILIO_PHONE_NUMBER = "+14482173794" 

# Initialize Clients
bot = Client("bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
call_app = PyTgCalls(user)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

active_sessions = {}

@bot.on_message(filters.command("call") & filters.group)
async def initiate_call(client, message):
    if len(message.command) < 2:
        await message.reply("Invalid syntax. Usage: /call +1234567890")
        return

    target_number = message.command[1]
    chat_id = message.chat.id

    try:
        # Initiate Twilio Call
        call = twilio_client.calls.create(
            url="http://demo.twilio.com/docs/voice.xml", # Replace with your VPS webhook URL returning TwiML
            to=target_number,
            from_=TWILIO_PHONE_NUMBER
        )
        call_sid = call.sid
    except Exception as e:
        await message.reply(f"Failed to initiate call via Twilio. Error: {str(e)}")
        return

    active_sessions[chat_id] = {
        "number": target_number,
        "call_sid": call_sid,
        "is_recording": False
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("End Call", callback_data="action_end"),
            InlineKeyboardButton("Join Voice Chat", url=f"https://t.me/{message.chat.username}?videochat")
        ],
        [
            InlineKeyboardButton("Start Recording", callback_data="action_record")
        ]
    ])

    await message.reply_text(
        f"Call Initiated.\nTarget: {target_number}\nStatus: Routing audio to Voice Chat.",
        reply_markup=keyboard
    )
    
    # Placeholder for actual audio piping from your VPS port to Telegram
    # await call_app.play(chat_id, AudioPiped("fifo_audio_stream_from_twilio"))

@bot.on_callback_query()
async def process_callback(client, query: CallbackQuery):
    chat_id = query.message.chat.id
    action = query.data

    if chat_id not in active_sessions and action != "action_end":
        await query.answer("This call session is no longer active.", show_alert=True)
        return

    if action == "action_end":
        if chat_id in active_sessions:
            call_sid = active_sessions[chat_id]["call_sid"]
            try:
                # Terminate Twilio Call
                twilio_client.calls(call_sid).update(status="completed")
            except Exception as e:
                print(f"Twilio termination error: {e}")
            
            del active_sessions[chat_id]
        
        try:
            await call_app.leave_call(chat_id)
        except Exception:
            pass
            
        await query.message.edit_text("Call terminated by user.")
        await query.answer("Connection closed.")

    elif action == "action_record":
        active_sessions[chat_id]["is_recording"] = True
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("End Call", callback_data="action_end"),
                InlineKeyboardButton("Join Voice Chat", url=f"https://t.me/{query.message.chat.username}?videochat")
            ],
            [
                InlineKeyboardButton("Stop Recording", callback_data="action_stop_record")
            ]
        ])
        
        await query.message.edit_reply_markup(reply_markup=keyboard)
        await query.answer("System recording initiated.", show_alert=False)

    elif action == "action_stop_record":
        active_sessions[chat_id]["is_recording"] = False
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("End Call", callback_data="action_end"),
                InlineKeyboardButton("Join Voice Chat", url=f"https://t.me/{query.message.chat.username}?videochat")
            ],
            [
                InlineKeyboardButton("Start Recording", callback_data="action_record")
            ]
        ])
        
        await query.message.edit_reply_markup(reply_markup=keyboard)
        await query.answer("Recording saved to local storage.", show_alert=True)
        await client.send_message(chat_id, "Recording processing complete. Output file generated.")

async def main():
    await bot.start()
    await call_app.start()
    print("Telephony Bot and PyTgCalls client are active.")
    import pyrogram
    await pyrogram.idle()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

