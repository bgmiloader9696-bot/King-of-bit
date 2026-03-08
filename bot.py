import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import time

# ------------------ YAHAN APNA TOKEN AUR ID DALO ------------------
BOT_TOKEN = "8723966859:AAFH68kkE6ac8MjiBmQy8Qk21Hqt5Ovod_k"
ADMIN_ID = 6548871396
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

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class PredictionEngine:
    @staticmethod
    def get_color(entry):
        s = (entry.get('colour') or entry.get('color') or '').lower()
        n = int(entry.get('number', 0))
        if 'green' in s: return 'G'
        if 'red' in s: return 'R'
        return 'G' if n >= 5 else 'R'

    @staticmethod
    def analyze_streak(colors):
        streak = 1
        for i in range(1, len(colors)):
            if colors[i] == colors[0]: streak += 1
            else: break
        weight = {2:1.5, 3:2.8, 4:4.0, 5:5.0}.get(streak, 0.6)
        vote = 'R' if colors[0]=='G' else 'G' if streak>=2 else colors[0]
        return {'vote':vote, 'weight':weight, 'streak':streak}

    @staticmethod
    def analyze_zigzag(colors):
        zz = 0
        for i in range(min(7, len(colors)-1)):
            if colors[i] != colors[i+1]: zz+=1
        if zz>=6: return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':3.5, 'score':zz}
        if zz>=4: return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':1.8, 'score':zz}
        if zz<=1: return {'vote':colors[0], 'weight':1.5, 'score':zz}
        return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':0.5, 'score':zz}

    @staticmethod
    def analyze_balance(colors, window):
        win = colors[:window]
        g = win.count('G')
        r = window - g
        if window == 6:
            if g>=5: return {'vote':'R','weight':3.2}
            if r>=5: return {'vote':'G','weight':3.2}
            if g==4: return {'vote':'R','weight':1.8}
            if r==4: return {'vote':'G','weight':1.8}
            return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':0.6}
        elif window == 10:
            if g>=8: return {'vote':'R','weight':2.5}
            if r>=8: return {'vote':'G','weight':2.5}
            if g>=7: return {'vote':'R','weight':1.5}
            if r>=7: return {'vote':'G','weight':1.5}
            if g>r+1: return {'vote':'R','weight':1.0}
            if r>g+1: return {'vote':'G','weight':1.0}
            return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':0.4}
        else:
            if g>=16: return {'vote':'R','weight':2.0}
            if r>=16: return {'vote':'G','weight':2.0}
            if g>r+3: return {'vote':'R','weight':1.2}
            if r>g+3: return {'vote':'G','weight':1.2}
            return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':0.3}

    @staticmethod
    def analyze_momentum(colors):
        g_new = colors[:3].count('G')
        g_old = colors[3:6].count('G') if len(colors)>=6 else 0
        if g_new>=3 and g_old<=1: return {'vote':'R','weight':2.5}
        if g_new<=0 and g_old>=2: return {'vote':'G','weight':2.5}
        if g_new > g_old+1: return {'vote':'R','weight':1.3}
        if g_old > g_new+1: return {'vote':'G','weight':1.3}
        return {'vote':('R' if colors[0]=='G' else 'G'), 'weight':0.4}

    @staticmethod
    def analyze_patterns(colors):
        signals = []
        if len(colors)>=4:
            p = colors[:4]
            if p[0]==p[1] and p[2]==p[3] and p[0]!=p[2]:
                signals.append({'vote':p[0],'weight':2.0})
            elif p[0]==p[1] and p[0]!=p[2]:
                signals.append({'vote':('R' if p[0]=='G' else 'G'),'weight':1.8})
            elif p[0]!=p[1] and p[1]==p[2]:
                signals.append({'vote':('R' if p[0]=='G' else 'G'),'weight':1.2})
            else:
                signals.append({'vote':('R' if colors[0]=='G' else 'G'),'weight':0.3})
        if len(colors)>=3:
            t = colors[0]+colors[1]+colors[2]
            if t=='GGG': signals.append({'vote':'R','weight':3.0})
            elif t=='RRR': signals.append({'vote':'G','weight':3.0})
            elif t=='GRG': signals.append({'vote':'R','weight':2.0})
            elif t=='RGR': signals.append({'vote':'G','weight':2.0})
            elif t=='GGR': signals.append({'vote':'G','weight':1.2})
            elif t=='RRG': signals.append({'vote':'R','weight':1.2})
            else: signals.append({'vote':('R' if colors[0]=='G' else 'G'),'weight':0.5})
        return signals

    @staticmethod
    def aggregate(signals):
        g = sum(s['weight'] for s in signals if s['vote']=='G')
        r = sum(s['weight'] for s in signals if s['vote']=='R')
        total = g+r
        margin = abs(g-r)
        winner = 'G' if g>=r else 'R'
        agree = sum(1 for s in signals if s['vote']==winner)
        ratio = agree/len(signals) if signals else 0.5
        return {'winner':winner, 'margin':margin, 'total':total, 'agree_ratio':ratio}

    @staticmethod
    def confidence(agg, streak, zz):
        base = 50
        margin_boost = min(35, (agg['margin']/max(agg['total'],1))*70)
        agree_boost = (agg['agree_ratio']-0.5)*20
        conf = base + margin_boost + agree_boost
        if streak>=4: conf+=6
        if zz>=6: conf+=5
        return min(93, max(48, int(conf)))

    @staticmethod
    def should_skip(agg, streak):
        if streak>=2: return True, f"LOSS ×{streak}"
        if agg['agree_ratio']<0.52: return True, "SPLIT"
        if agg['margin']<1.5: return True, "LOW MARGIN"
        return False, ""

    def predict(self, api_data):
        try:
            items = api_data.get('data',{}).get('list',[])
            colors = [self.get_color(e) for e in items[:20]]
            if len(colors)<6: return None

            signals = []
            s1 = self.analyze_streak(colors); signals.append(s1)
            s2 = self.analyze_zigzag(colors); signals.append(s2)
            signals.append(self.analyze_balance(colors,6))
            signals.append(self.analyze_balance(colors,10))
            signals.append(self.analyze_balance(colors,20))
            signals.append(self.analyze_momentum(colors))
            signals.extend(self.analyze_patterns(colors))

            period = int(items[0].get('issueNumber',0))
            hash_val = (period*17 + (11 if colors[0]=='G' else 7) + (5 if colors[1]=='G' else 3)) % 100
            signals.append({'vote':'R' if hash_val<50 else 'G', 'weight':0.3})

            agg = self.aggregate(signals)
            skip, reason = self.should_skip(agg, s1['streak'])
            conf = self.confidence(agg, s1['streak'], s2['score'])

            next_period = str(int(items[0]['issueNumber'])+1)[-4:]

            if s2['score']>=5: pattern="ZIG-ZAG"
            elif s1['streak']>=3: pattern="STREAK"
            else: pattern="MIXED"

            return {
                'period': next_period,
                'prediction': 'SKIP' if skip else ('GREEN' if agg['winner']=='G' else 'RED'),
                'confidence': conf if not skip else 0,
                'streak': s1['streak'],
                'color': colors[0],
                'skip_reason': reason if skip else None,
                'pattern': pattern,
                'agree': int(agg['agree_ratio']*100),
                'margin': round(agg['margin'],1)
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
            user_data[uid] = {'30S':False, '1M':False, 'history':[], 'wins':0, 'losses':0, 'skips':0}
            await self.show_main_menu(update.message)
            return
        
        if uid in approved_users:
            await self.show_main_menu(update.message)
        else:
            if uid not in pending_requests:
                pending_requests[uid] = {
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name
                }
                keyboard = [[
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{uid}"),
                    InlineKeyboardButton("❌ DECLINE", callback_data=f"decline_{uid}")
                ]]
                info = f"Name: {user.first_name} {user.last_name or ''}\nUsername: @{user.username or 'N/A'}\nUser ID: {uid}"
                await context.bot.send_message(
                    chat_id=self.admin_id,
                    text=f"🔔 *New User Request*\n\n{info}",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
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
                user_data[target] = {'30S':False, '1M':False, 'history':[], 'wins':0, 'losses':0, 'skips':0}
                await context.bot.send_message(
                    chat_id=target,
                    text="✅ *Your request has been APPROVED!*\n\nSend /start to begin.",
                    parse_mode='Markdown'
                )
                await query.edit_message_text(f"✅ User {target} approved successfully!")
            else:
                if target in pending_requests:
                    del pending_requests[target]
                await context.bot.send_message(
                    chat_id=target,
                    text="❌ *Your request has been DECLINED*",
                    parse_mode='Markdown'
                )
                await query.edit_message_text(f"❌ User {target} declined.")
            return

        if uid not in approved_users:
            await query.edit_message_text("❌ You are not approved. Send /start to request access.")
            return
        
        if uid not in user_data:
            user_data[uid] = {'30S':False, '1M':False, 'history':[], 'wins':0, 'losses':0, 'skips':0}

        if query.data == "start_prediction":
            keyboard = [
                [InlineKeyboardButton("⚡ 30 SECONDS", callback_data="mode_30S")],
                [InlineKeyboardButton("⏱️ 1 MINUTE", callback_data="mode_1M")]
            ]
            await query.edit_message_text("⚡ *SELECT MODE* ⚡\n\nChoose your prediction time:", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("mode_"):
            mode = query.data.replace("mode_", "")
            for m in ['30S','1M']:
                if user_data[uid][m]:
                    user_data[uid][m] = False
                    task_key = f"{uid}_{m}"
                    if task_key in running_tasks:
                        running_tasks[task_key].cancel()
                        del running_tasks[task_key]
            
            user_data[uid][mode] = True
            task_key = f"{uid}_{mode}"
            if task_key not in running_tasks:
                task = asyncio.create_task(self.prediction_loop(uid, mode, context))
                running_tasks[task_key] = task
            
            await query.edit_message_text(f"⚡ *{mode} STARTED*\n\nPredictions will appear shortly...", parse_mode='Markdown')

        elif query.data == "stop_prediction":
            for m in ['30S','1M']:
                if user_data[uid][m]:
                    user_data[uid][m] = False
                    task_key = f"{uid}_{m}"
                    if task_key in running_tasks:
                        running_tasks[task_key].cancel()
                        del running_tasks[task_key]
            await query.edit_message_text("⏹️ *All predictions STOPPED*", parse_mode='Markdown')
            await asyncio.sleep(1)
            await self.show_main_menu(query.message)

        elif query.data == "prediction_now":
            text = "🔮 *CURRENT PREDICTIONS*\n\n"
            for mode in ['30S','1M']:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(API_URLS[mode]) as resp:
                            data = await resp.json()
                    pred = self.engine.predict(data)
                    if pred:
                        if pred['prediction'] == 'SKIP':
                            text += f"{'⚡' if mode=='30S' else '⏱️'} {mode}: `{pred['period']}` ⏭️ SKIP\n"
                        else:
                            emoji = "🟢" if pred['prediction']=='GREEN' else "🔴"
                            text += f"{'⚡' if mode=='30S' else '⏱️'} {mode}: `{pred['period']}` {emoji} {pred['prediction']} ({pred['confidence']}%)\n"
                except:
                    text += f"{'⚡' if mode=='30S' else '⏱️'} {mode}: ❌ ERROR\n"
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "analysis":
            text = "📊 *ANALYSIS*\n\n"
            for mode in ['30S','1M']:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(API_URLS[mode]) as resp:
                            data = await resp.json()
                    items = data.get('data',{}).get('list',[])
                    colors = [self.engine.get_color(e) for e in items[:10]]
                    if colors:
                        g = colors.count('G')
                        r = colors.count('R')
                        streak = 1
                        for i in range(1,len(colors)):
                            if colors[i]==colors[0]: streak+=1
                            else: break
                        text += f"{'⚡' if mode=='30S' else '⏱️'} *{mode}*\nG:{g} R:{r} | {streak}{colors[0]}\n\n"
                except:
                    text += f"{mode}: ❌\n\n"
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "history":
            hist = user_data[uid]['history'][-10:]
            text = "📜 *HISTORY*\n\n"
            if hist:
                for h in reversed(hist):
                    if h['result'] == 'WIN':
                        text += f"`{h['period']}` 🟢 WIN [{h['mode']}] {h.get('confidence',0)}%\n"
                    elif h['result'] == 'LOSS':
                        text += f"`{h['period']}` 🔴 LOSS [{h['mode']}] {h.get('confidence',0)}%\n"
                    elif h['result'] == 'SKIP':
                        text += f"`{h['period']}` ⏭️ SKIP [{h['mode']}]\n"
                    else:
                        text += f"`{h['period']}` ⏳ PENDING [{h['mode']}]\n"
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
            win_rate = (wins/total*100) if total>0 else 0
            text = f"📈 *STATS*\n\nWins: {wins}\nLosses: {losses}\nSkips: {skips}\nWin Rate: {win_rate:.1f}%"
            keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "main_menu":
            await self.show_main_menu(query.message)

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
                    
                    if pred['prediction'] == 'SKIP':
                        msg = f"🤖 *AI ENGINE*\n\nPERIOD\n`{pred['period']}`\n\nPREDICTION\n⏭️ SKIP\n\nWIN {wins} | ✖ LOSS {losses}"
                        user_data[uid]['skips'] += 1
                        result_status = 'SKIP'
                    else:
                        emoji = "🟢" if pred['prediction'] == 'GREEN' else "🔴"
                        msg = f"🤖 *AI ENGINE*\n\nPERIOD\n`{pred['period']}`\n\nPREDICTION\n{emoji} {pred['prediction']}\n\nCONFIDENCE\n{pred['confidence']}%\n\nWIN {wins} | ✖ LOSS {losses}"
                        result_status = 'PENDING'
                    
                    await context.bot.send_message(chat_id=uid, text=msg, parse_mode='Markdown')
                    
                    user_data[uid]['history'].append({
                        'period': pred['period'],
                        'prediction': pred['prediction'],
                        'confidence': pred['confidence'],
                        'mode': mode,
                        'result': result_status
                    })
                    
                    if len(user_data[uid]['history']) > 50:
                        user_data[uid]['history'] = user_data[uid]['
