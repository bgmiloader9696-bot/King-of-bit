import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import time

# ------------------ YAHAN APNA TOKEN AUR ID DALO ------------------
BOT_TOKEN = "8723966859:AAFH68kkE6ac8MjiBmQy8Qk21Hqt5Ovod_k"
ADMIN_ID = 7983241359
# -----------------------------------------------------------------

API_URLS = {
    '30S': 'https://draw.ar-lottery01.com/WinGo/WinGo_30S/GetHistoryIssuePage.json',
    '1M': 'https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json'
}

pending_requests = {}
approved_users = set()
user_data = {}
running_tasks = {}
user_last_click = {}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class PredictionEngine:
    @staticmethod
    def get_color(entry):
        s = (entry.get('colour') or entry.get('color') or '').lower()
        n = int(entry.get('number', 0))
        if 'green' in s: return 'G'
        if 'red' in s: return 'R'
        return 'G' if n >= 5 else 'R'

    def predict(self, api_data):
        try:
            items = api_data.get('data', {}).get('list', [])
            colors = [self.get_color(e) for e in items[:20]]
            if len(colors) < 6: return None
            next_period = str(int(items[0]['issueNumber']) + 1)[-4:]
            
            # Simple prediction logic (GREEN/RED randomly for testing)
            import random
            pred = random.choice(['GREEN', 'RED'])
            conf = random.randint(65, 92)
            
            return {
                'period': next_period,
                'prediction': pred,
                'confidence': conf
            }
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return None

class TelegramBot:
    def __init__(self, token, admin_id):
        self.token = token
        self.admin_id = admin_id
        self.engine = PredictionEngine()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        uid = user.id
        
        if uid == self.admin_id:
            approved_users.add(uid)
            user_data[uid] = {'30S': False, '1M': False, 'history': [], 'wins': 0, 'losses': 0, 'skips': 0}
            await self.show_main_menu(update.message)
            return
        
        if uid in approved_users:
            await self.show_main_menu(update.message)
        else:
            if uid not in pending_requests:
                pending_requests[uid] = {'username': user.username, 'first_name': user.first_name, 'last_name': user.last_name}
                keyboard = [[InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{uid}"), InlineKeyboardButton("❌ DECLINE", callback_data=f"decline_{uid}")]]
                info = f"Name: {user.first_name} {user.last_name or ''}\nUsername: @{user.username or 'N/A'}\nUser ID: {uid}"
                await context.bot.send_message(chat_id=self.admin_id, text=f"🔔 *New User Request*\n\n{info}", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
                await update.message.reply_text("⏳ *Request Sent to Admin*\n\nPlease wait for approval...", parse_mode='Markdown')
            else:
                await update.message.reply_text("⏳ Your request is already pending. Please wait for admin approval.", parse_mode='Markdown')

    async def show_main_menu(self, message):
        keyboard = [
            [InlineKeyboardButton("▶️ START PREDICTION", callback_data="start_prediction")],
            [InlineKeyboardButton("⏹️ STOP PREDICTION", callback_data="stop_prediction")],
            [InlineKeyboardButton("🔮 PREDICTION", callback_data="prediction_now")],
            [InlineKeyboardButton("📊 ANALYSIS", callback_data="analysis")],
            [InlineKeyboardButton("📜 HISTORY", callback_data="history")],
            [InlineKeyboardButton("📈 STATS", callback_data="stats")]
        ]
        await message.reply_text("⚡ *NEURAL ULTIMATE* ⚡", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        uid = query.from_user.id

        if query.data.startswith("approve_") or query.data.startswith("decline_"):
            if uid != self.admin_id:
                await query.edit_message_text("❌ Unauthorized")
                return
            target = int(query.data.split("_")[1])
            if query.data.startswith("approve_"):
                approved_users.add(target)
                user_data[target] = {'30S': False, '1M': False, 'history': [], 'wins': 0, 'losses': 0, 'skips': 0}
                await context.bot.send_message(chat_id=target, text="✅ *Your request has been APPROVED!*\n\nSend /start to begin.", parse_mode='Markdown')
                await query.edit_message_text(f"✅ User {target} approved successfully!")
            else:
                if target in pending_requests: del pending_requests[target]
                await context.bot.send_message(chat_id=target, text="❌ *Your request has been DECLINED*", parse_mode='Markdown')
                await query.edit_message_text(f"❌ User {target} declined.")
            return

        if uid not in approved_users:
            await query.edit_message_text("❌ You are not approved. Send /start to request access.")
            return
        
        if uid not in user_data:
            user_data[uid] = {'30S': False, '1M': False, 'history': [], 'wins': 0, 'losses': 0, 'skips': 0}

        if query.data == "start_prediction":
            keyboard = [[InlineKeyboardButton("⚡ 30 SECONDS", callback_data="mode_30S")], [InlineKeyboardButton("⏱️ 1 MINUTE", callback_data="mode_1M")]]
            await query.edit_message_text("⚡ *SELECT MODE* ⚡\n\nChoose your prediction time:", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("mode_"):
            mode = query.data.replace("mode_", "")
            for m in ['30S', '1M']:
                if user_data[uid][m]:
                    user_data[uid][m] = False
                    if f"{uid}_{m}" in running_tasks:
                        running_tasks[f"{uid}_{m}"].cancel()
                        del running_tasks[f"{uid}_{m}"]
            user_data[uid][mode] = True
            if f"{uid}_{mode}" not in running_tasks:
                running_tasks[f"{uid}_{mode}"] = asyncio.create_task(self.prediction_loop(uid, mode, context))
            await query.edit_message_text(f"⚡ *{mode} STARTED*\n\nPredictions will appear shortly...", parse_mode='Markdown')

        elif query.data == "stop_prediction":
            for m in ['30S', '1M']:
                if user_data[uid][m]:
                    user_data[uid][m] = False
                    if f"{uid}_{m}" in running_tasks:
                        running_tasks[f"{uid}_{m}"].cancel()
                        del running_tasks[f"{uid}_{m}"]
            await query.edit_message_text("⏹️ *All predictions STOPPED*", parse_mode='Markdown')
            await asyncio.sleep(1)
            await self.show_main_menu(query.message)

        elif query.data == "prediction_now":
            text = "🔮 *CURRENT PREDICTIONS*\n\n"
            for mode in ['30S', '1M']:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(API_URLS[mode]) as resp:
                            data = await resp.json()
                    pred = self.engine.predict(data)
                    if pred:
                        text += f"{'⚡' if mode=='30S' else '⏱️'} {mode}: `{pred['period']}` {'🟢' if pred['prediction']=='GREEN' else '🔴'} {pred['prediction']} ({pred['confidence']}%)\n"
                except:
                    text += f"{'⚡' if mode=='30S' else '⏱️'} {mode}: ❌ ERROR\n"
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "history":
            hist = user_data[uid]['history'][-10:]
            text = "📜 *HISTORY*\n\n"
            if hist:
                for h in reversed(hist):
                    if h['result'] == 'WIN': emoji = "🟢"
                    elif h['result'] == 'LOSS': emoji = "🔴"
                    elif h['result'] == 'SKIP': emoji = "⏭️"
                    else: emoji = "⏳"
                    text += f"`{h['period']}` {emoji} {h['result']} [{h['mode']}]\n"
                text += f"\n✅ Wins: {user_data[uid]['wins']}  ❌ Losses: {user_data[uid]['losses']}  ⏭️ Skips: {user_data[uid]['skips']}"
            else:
                text += "No history yet"
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "stats":
            wins = user_data[uid]['wins']
            losses = user_data[uid]['losses']
            skips = user_data[uid]['skips']
            total = wins + losses
            win_rate = (wins / total * 100) if total > 0 else 0
            text = f"📈 *STATS*\n\nWins: {wins}\nLosses: {losses}\nSkips: {skips}\nWin Rate: {win_rate:.1f}%"
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "main_menu":
            await self.show_main_menu(query.message)

        elif query.data == "analysis":
            text = "📊 *ANALYSIS*\n\nSimple analysis here..."
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    async def prediction_loop(self, uid, mode, context):
        interval = 30 if mode == '30S' else 60
        last_period = None
        while user_data.get(uid, {}).get(mode, False):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(API_URLS[mode]) as resp:
                        data = await resp.json()
                pred = self.engine.predict(data)
                if pred and pred['period'] != last_period:
                    last_period = pred['period']
                    wins = user_data[uid]['wins']
                    losses = user_data[uid]['losses']
                    emoji = "🟢" if pred['prediction'] == 'GREEN' else "🔴"
                    msg = f"🤖 *AI ENGINE*\n\nPERIOD\n`{pred['period']}`\n\nPREDICTION\n{emoji} {pred['prediction']}\n\nCONFIDENCE\n{pred['confidence']}%\n\nWIN {wins} | ✖ LOSS {losses}"
                    await context.bot.send_message(chat_id=uid, text=msg, parse_mode='Markdown')
                    user_data[uid]['history'].append({'period': pred['period'], 'prediction': pred['prediction'], 'mode': mode, 'result': 'PENDING'})
                    if len(user_data[uid]['history']) > 50:
                        user_data[uid]['history'] = user_data[uid]['history'][-50:]
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Prediction loop error: {e}")
                await asyncio.sleep(5)

    def run(self):
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CallbackQueryHandler(self.button_handler))
        print("✅ Bot is running...")
        app.run_polling()

if __name__ == "__main__":
    print("✅ Neural Ultimate Bot Starting...")
    print(f"👑 Admin ID: {ADMIN_ID}")
    bot = TelegramBot(BOT_TOKEN, ADMIN_ID)
    bot.run()
