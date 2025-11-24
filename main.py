import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests
import pandas as pd
import pandas_ta as ta
import datetime
import os
import json
import asyncio
import pytz 
from dateutil import parser

# ================= 配置区域 =================
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') 

# === 💾 Railway 持久化路径 ===
BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist_v5.json")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

watch_data = {}

# ================= 数据存取 =================
def load_data():
    global watch_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                watch_data = json.load(f)
            print(f"📚 已加载数据")
        except: watch_data = {}
    else:
        watch_data = {}
        save_data()

def save_data():
    try:
        with open(DATA_FILE, 'w') as f: json.dump(watch_data, f, indent=4)
    except Exception as e: print(f"❌ 保存失败: {e}")

# ================= 🧠 战法说明书 =================
def get_signal_advice(t):
    advice = ""
    if "财报" in t: advice = "高危事件: 锁定未来2周内财报，不确定性极大，建议回避！"
    elif "DCF" in t: advice = "价值回归: 价格偏离内在价值，关注长期安全边际。"
    elif "PEG" in t: advice = "成长性价比: 结合增速看估值。"
    elif "PS" in t: advice = "市销率: 适用于亏损成长股的估值锚点。"
    elif "PE" in t: advice = "市盈率: 传统估值指标出现极值。"
    elif "九转" in t and "买" in t: advice = "九转底部: 连跌9天，极度超跌反弹信号。"
    elif "九转" in t and "卖" in t: advice = "九转顶部: 连涨9天，情绪过热回调信号。"
    elif "十三转" in t: advice = "趋势衰竭: 趋势走到尽头，变盘在即。"
    elif "爆量" in t: advice = "资金异动: 主力大举进出，非散户行为。"
    elif "放量" in t: advice = "量价配合: 趋势健康。"
    elif "缩量" in t: advice = "洗盘/背离: 关注变盘。"
    elif "早晨" in t or "锤子" in t: advice = "底部形态: 多头抵抗。"
    elif "黄昏" in t or "断头" in t: advice = "顶部形态: 空头反扑。"
    elif "多头" in t: advice = "最强趋势: 均线发散向上，顺势而为。"
    elif "空头" in t: advice = "最弱趋势: 均线发散向下，空仓观望。"
    elif "Nx" in t: advice = "通道交易: 关注通道突破与支撑。"
    elif "背离" in t: advice = "先行指标: 动能衰竭。"
    elif "超买" in t or "超卖" in t: advice = "情绪极值: 获利盘/恐慌盘涌出。"
    return advice

# ================= ⚖️ 评分系统 =================
def get_signal_category_and_score(s):
    s = s.strip()
    if "财报" in s: return 'fundamental', 0 
    if "DCF" in s: return 'fundamental', 3 if "低估" in s else -3
    if "PEG" in s: return 'fundamental', 2 if "低估" in s else -2
    if "PS" in s: return 'fundamental', 2 if "低估" in s else -2
    if "PE" in s: return 'fundamental', 2 if "低估" in s else -2

    if "九转" in s or "十三转" in s:
        return 'timing', 4 if ("买入" in s or "底部" in s) else -4
    if "盘中爆量" in s: return 'volume', 4 if "抢筹" in s else -4
    if "放量" in s: return 'volume', 3 if "大涨" in s else -3
    if "缩量" in s: return 'volume', 1 if "回调" in s else -1
    
    p_bull = ["早晨", "阳包阴", "锤子"]; p_bear = ["断头", "阴包阳", "射击", "黄昏", "墓碑"]
    if any(x in s for x in p_bull): return 'pattern', 4
    if any(x in s for x in p_bear): return 'pattern', -4
    
    t_bull_3 = ["多头排列", "年线", "唐奇安上"]
    t_bear_3 = ["空头排列", "年线", "唐奇安下"]
    t_bull_2 = ["Nx 突破", "Nx 站稳", "Nx 牛市", "R1"]
    t_bear_2 = ["Nx 跌破", "Nx 熊市", "S1"]
    if any(x in s for x in t_bull_3): return 'trend', 3
    if any(x in s for x in t_bear_3): return 'trend', -3
    if any(x in s for x in t_bull_2): return 'trend', 2
    if any(x in s for x in t_bear_2): return 'trend', -2
    if "站上" in s: return 'trend', 1
    if "跌破" in s: return 'trend', -1
    
    if "背离" in s: return 'oscillator', 3 if "底" in s else -3
    if "金叉" in s or "布林" in s or "超卖" in s: return 'oscillator', 1
    if "死叉" in s or "超买" in s: return 'oscillator', -1
    
    return 'other', 0

def generate_report_content(signals):
    items = []
    for s in signals:
        cat, score = get_signal_category_and_score(s)
        items.append({'raw': s, 'cat': cat, 'score': score, 'active': False})

    for item in items:
        if item['cat'] in ['volume', 'timing', 'fundamental']:
            item['active'] = True

    for cat in ['trend', 'pattern', 'oscillator']:
        cat_items = [i for i in items if i['cat'] == cat]
        if cat_items:
            best = max(cat_items, key=lambda x: abs(x['score']))
            best['active'] = True

    total_score = 0
    active_blocks = [] 
    inactive_lines = []
    
    for item in items:
        score_val = item['score']
        score_str = f"+{score_val}" if score_val > 0 else f"{score_val}"
        
        if item['active']:
            total_score += score_val
            block = f"### {item['raw']} ({score_str})"
            advice = get_signal_advice(item['raw'])
            if advice: block += f"\n> {advice}"
            active_blocks.append(block)
        else:
            if score_val != 0:
                inactive_lines.append(f"🔸 {item['raw']} ({score_str}) [已去重]")

    final_text = "\n".join(active_blocks)
    if inactive_lines: final_text += "\n\n" + "\n".join(inactive_lines)
    return total_score, final_text

def format_dashboard_title(score):
    count = int(min(abs(score), 8))
    icons = "⭐" * count if score > 0 else "💀" * count if score < 0 else "⚖️"
    status, color = "震荡", discord.Color.light_grey()
    if score >= 8: status, color = "史诗暴涨", discord.Color.from_rgb(255, 0, 0)
    elif score >= 4: status, color = "极度强势", discord.Color.red()
    elif score >= 1: status, color = "趋势看多", discord.Color.orange()
    elif score <= -8: status, color = "史诗崩盘", discord.Color.from_rgb(0, 255, 0)
    elif score <= -4: status, color = "极度高危", discord.Color.green()
    elif score <= -1: status, color = "趋势看空", discord.Color.dark_teal()
    else: status, color = "震荡整理", discord.Color.gold()
    return f"{status} ({score:+}) {icons}", color

# ================= FMP API =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

# V8.6: 强制 Stable 节点
def get_valuation_and_earnings(ticker, current_price):
    if not FMP_API_KEY: return [], "Key Missing"
    sigs = []
    debug_log = {} 
    
    try:
        # 1. 📅 财报日历 (Stable Endpoint)
        today = datetime.date.today()
        future_str = (today + datetime.timedelta(days=14)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')
        
        # ⚠️ 注意: Stable 接口是 earnings-calendar (复数)
        cal_url = f"https://financialmodelingprep.com/stable/earnings-calendar?from={today_str}&to={future_str}&apikey={FMP_API_KEY}"
        cal_resp = requests.get(cal_url, timeout=10)
        
        debug_log['Cal'] = cal_resp.status_code
        
        if cal_resp.status_code == 200:
            cal_data = cal_resp.json()
            for entry in cal_data:
                sym = entry.get('symbol', '')
                if ticker == sym or sym == f"{ticker}.US":
                    d_str = entry.get('date')
                    if d_str:
                        e_date = parser.parse(d_str).date()
                        diff = (e_date - today).days
                        debug_log['NextE'] = f"{d_str}({diff})"
                        if 0 <= diff <= 14: 
                            sigs.append(f"⚠️ 财报预警 (T-{diff}天)")
                        break 

        # 2. 💎 估值 (Stable Ratios TTM)
        # ⚠️ 注意: Stable 接口使用 query param: ?symbol=AAPL
        r_url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={FMP_API_KEY}"
        r_resp = requests.get(r_url, timeout=10)
        
        debug_log['Rat'] = r_resp.status_code
        
        if r_resp.status_code == 200:
            r_data = r_resp.json()
            if r_data:
                rd = r_data[0]
                peg = rd.get('priceToEarningsGrowthRatioTTM')
                ps = rd.get('priceToSalesRatioTTM')
                pe = rd.get('priceToEarningsRatioTTM')
                eps_ttm = rd.get('netIncomePerShareTTM', 0)
                
                debug_log['PEG'] = peg
                debug_log['PS'] = ps
                
                if eps_ttm > 0:
                    if peg is not None:
                        if 0 < peg < 1.2: sigs.append(f"💎 PEG 低估 ({peg:.2f})")
                        elif peg > 2.5: sigs.append(f"💎 PEG 高估 ({peg:.2f})")
                    if pe is not None:
                        if 0 < pe < 20: sigs.append(f"💎 PE 低估 ({pe:.1f}x)")
                        elif pe > 60: sigs.append(f"💎 PE 泡沫 ({pe:.1f}x)")
                else:
                    if ps is not None:
                        if ps < 2.0: sigs.append(f"💎 PS 低估 ({ps:.2f}x)")
                        elif ps > 12: sigs.append(f"💎 PS 泡沫 ({ps:.2f}x)")

        # 3. DCF (Stable DCF)
        # ⚠️ 注意: Stable 接口使用 query param
        d_url = f"https://financialmodelingprep.com/stable/discounted-cash-flow?symbol={ticker}&apikey={FMP_API_KEY}"
        d_resp = requests.get(d_url, timeout=10)
        
        debug_log['DCF'] = d_resp.status_code
        
        if d_resp.status_code == 200:
            d_data = d_resp.json()
            if d_data and len(d_data) > 0 and 'dcf' in d_data[0]:
                dcf = d_data[0]['dcf']
                debug_log['Val'] = dcf
                if dcf > 0:
                    if current_price < dcf * 0.85: sigs.append(f"💎 DCF 低估 (${dcf:.1f})")
                    elif current_price > dcf * 1.4: sigs.append(f"💎 DCF 高估 (${dcf:.1f})")

    except Exception as e:
        debug_log['Err'] = str(e)
        
    debug_str = " | ".join([f"{k}:{v}" for k,v in debug_log.items()])
    return sigs, debug_str

def get_daily_data_stable(ticker):
    if not FMP_API_KEY: return None
    try:
        # 使用 Stable 历史价格接口
        hist_url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={ticker}&apikey={FMP_API_KEY}"
        hist_resp = requests.get(hist_url, timeout=10)
        if hist_resp.status_code != 200: return None
        hist_data = hist_resp.json()
        if not hist_data: return None
        df = pd.DataFrame(hist_data)
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df = df.iloc[::-1].reset_index(drop=True)
        
        # 使用 Stable Quote 接口
        quote_url = f"https://financialmodelingprep.com/stable/quote?symbol={ticker}&apikey={FMP_API_KEY}"
        quote_resp = requests.get(quote_url, timeout=5)
        quote_data = quote_resp.json()
        if not quote_data: return None
        curr = quote_data[0]
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        last_hist_date = df['date'].iloc[-1]
        if last_hist_date == today_str:
            idx = df.index[-1]
            df.loc[idx, 'close'] = curr['price']
            df.loc[idx, 'high'] = max(df.loc[idx, 'high'], curr['price']) 
            df.loc[idx, 'low'] = min(df.loc[idx, 'low'], curr['price'])
            df.loc[idx, 'volume'] = curr.get('volume', df.loc[idx, 'volume'])
        else:
            new_row = {'date': today_str, 'open': curr.get('open', df['close'].iloc[-1]), 'high': curr.get('dayHigh', curr['price']), 'low': curr.get('dayLow', curr['price']), 'close': curr['price'], 'volume': curr.get('volume', 0)}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        print(f"❌ 数据处理异常 {ticker}: {e}")
        return None

def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 250: return None, None, "No Data"
    signals = []
    
    df['nx_blue_up'] = df['high'].ewm(span=24, adjust=False).mean()
    df['nx_blue_dw'] = df['low'].ewm(span=23, adjust=False).mean()
    df['nx_yell_up'] = df['high'].ewm(span=89, adjust=False).mean()
    df['nx_yell_dw'] = df['low'].ewm(span=90, adjust=False).mean()
    mas = [5, 10, 20, 30, 60, 120, 200]
    for m in mas: df.ta.sma(length=m, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.rsi(length=14, append=True)
    try: df.ta.kdj(length=9, signal=3, append=True)
    except: pass
    df.ta.willr(length=14, append=True); df.ta.cci(length=20, append=True)
    df.ta.adx(length=14, append=True); df.ta.obv(append=True)
    df.ta.atr(length=14, append=True); df.ta.donchian(lower_length=20, upper_length=20, append=True)
    try: df.ta.pivots(type="fibonacci", append=True)
    except: pass
    
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.columns = [str(c).upper() for c in df.columns]

    curr = df.iloc[-1]; prev = df.iloc[-2]; 
    price = curr['CLOSE']

    # --- 0. 估值/财报 (V8.6 Stable 修正) ---
    val_sigs, debug_info = get_valuation_and_earnings(ticker, price)
    signals.extend(val_sigs)

    # --- 1. 内置算法: 九转/十三转 ---
    try:
        work_df = df.iloc[-50:].copy()
        c = work_df['CLOSE'].values
        h = work_df['HIGH'].values
        l = work_df['LOW'].values
        buy_setup = 0; sell_setup = 0
        for i in range(4, len(c)):
            if c[i] > c[i-4]: sell_setup += 1; buy_setup = 0
            elif c[i] < c[i-4]: buy_setup += 1; sell_setup = 0
            else: buy_setup = 0; sell_setup = 0
        if buy_setup == 9: signals.append("神奇九转: 底部买入信号 (9)")
        elif sell_setup == 9: signals.append("神奇九转: 顶部卖出信号 (9)")
        if buy_setup == 13: signals.append("迪玛克十三转: 终极底部 (13)")
        elif sell_setup == 13: signals.append("迪玛克十三转: 终极顶部 (13)")
    except Exception as e: print(f"Algo Error: {e}")

    # Nx
    is_break_blue = prev['CLOSE'] < prev['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_BLUE_UP']
    if curr['CLOSE'] > curr['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_YELL_UP']:
        if is_break_blue: signals.append("Nx 突破双梯")
        elif curr['CLOSE'] > curr['NX_BLUE_DW']: signals.append("Nx 站稳蓝梯")
    if curr['NX_BLUE_DW'] > curr['NX_YELL_UP']: signals.append("Nx 牛市排列")
    elif curr['NX_YELL_DW'] > curr['NX_BLUE_UP']: signals.append("Nx 熊市压制")

    # Vol
    vol_ma = curr['VOL_MA_20']
    if pd.notna(vol_ma) and vol_ma > 0:
        rvol = curr['VOLUME'] / vol_ma
        if rvol > 2.0 and curr['CLOSE'] > prev['CLOSE']: signals.append(f"放量大涨 (量比:{rvol:.1f}x)")
        elif rvol > 2.0 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"放量大跌 (量比:{rvol:.1f}x)")
        elif rvol < 0.6 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"缩量回调 (量比:{rvol:.1f}x)")

    # Pattern
    if 'P_FIB_R1' in df.columns and prev['CLOSE'] < curr['P_FIB_R1'] and curr['CLOSE'] > curr['P_FIB_R1']: signals.append(f"突破 R1 阻力")
    if curr['CLOSE'] > prev['DCU_20_20']: signals.append(f"突破唐奇安上轨")
    if (curr['SMA_5'] > curr['SMA_10'] > curr['SMA_20'] > curr['SMA_60']): signals.append("均线多头排列")
    if (curr['SMA_5'] < curr['SMA_10'] < curr['SMA_20'] < curr['SMA_60']): signals.append("均线空头排列")
    if prev['CLOSE'] < prev['SMA_200'] and curr['CLOSE'] > curr['SMA_200']: signals.append("🐂 突破年线 MA200")
    if prev['CLOSE'] > prev['SMA_200'] and curr['CLOSE'] < curr['SMA_200']: signals.append("🐻 跌破年线 MA200")
    if curr['RSI_14'] > 75: signals.append(f"RSI 超买 ({curr['RSI_14']:.1f})")
    elif curr['RSI_14'] < 30: signals.append(f"RSI 超卖 ({curr['RSI_14']:.1f})")
    body = abs(curr['CLOSE'] - curr['OPEN'])
    lower_shadow = min(curr['CLOSE'], curr['OPEN']) - curr['LOW']
    if body > 0 and lower_shadow > (body * 2) and curr['RSI_14'] < 50: signals.append("锤子线")
    if prev['CLOSE'] < prev['OPEN'] and curr['CLOSE'] > curr['OPEN'] and curr['OPEN'] < prev['CLOSE'] and curr['CLOSE'] > prev['OPEN']: signals.append("阳包阴")

    return price, signals, debug_info

# ================= Bot 指令集 =================
@bot.event
async def on_ready():
    load_data()
    print(f'✅ V8.6 终极修正版Bot已启动 (强制Stable节点): {bot.user}')
    await bot.tree.sync()
    if not daily_monitor.is_running(): daily_monitor.start()

@bot.tree.command(name="help_bot", description="显示指令手册")
async def help_bot(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 指令手册 (V8.6)", color=discord.Color.blue())
    embed.add_field(name="🔒 隐私说明", value="您添加的列表仅自己可见，Bot会单独艾特您推送。", inline=False)
    embed.add_field(name="📋 监控", value="`/add [代码]` : 添加自选\n`/remove [代码]` : 删除自选\n`/list` : 查看我的列表", inline=False)
    embed.add_field(name="🔎 临时查询", value="`/check [代码]` : 立刻分析", inline=False)
    embed.set_footer(text="FMP Ultimate API • 机构级多因子模型")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="check", description="立刻分析股票")
async def check_stocks(interaction: discord.Interaction, tickers: str):
    await interaction.response.defer()
    stock_list = tickers.upper().replace(',', ' ').split()[:5]
    for ticker in stock_list:
        price, signals, debug_info = analyze_daily_signals(ticker)
        if price is None:
            await interaction.followup.send(f"❌ 无法获取 {ticker} 数据")
            continue
        if not signals: signals.append("趋势平稳，暂无异动")
        
        score, desc_final = generate_report_content(signals)
        text_part, color = format_dashboard_title(score)
        
        embed = discord.Embed(title=f"{ticker} : {text_part}", description=f"**现价**: ${price:.2f}\n\n{desc_final}", color=color)
        embed.set_image(url=get_finviz_chart_url(ticker))
        
        ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
        footer_text = f"FMP Ultimate API • [DEBUG] {debug_info}"
        if len(footer_text) > 2000: footer_text = footer_text[:2000]
        embed.set_footer(text=footer_text)
        
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="add", description="添加个人监控")
@app_commands.choices(mode=[app_commands.Choice(name="每日一次", value="once_daily"), app_commands.Choice(name="总是提醒", value="always")])
async def add_stock(interaction: discord.Interaction, ticker: str, mode: str = "once_daily"):
    ticker = ticker.upper()
    user_id = str(interaction.user.id)
    if user_id not in watch_data: watch_data[user_id] = {}
    watch_data[user_id][ticker] = {"mode": mode, "last_alert_date": ""}
    save_data()
    await interaction.response.send_message(f"✅ 已为您个人添加 **{ticker}**。")

@bot.tree.command(name="remove", description="删除个人监控")
async def remove_stock(interaction: discord.Interaction, ticker: str):
    ticker = ticker.upper()
    user_id = str(interaction.user.id)
    if user_id in watch_data and ticker in watch_data[user_id]:
        del watch_data[user_id][ticker]
        if not watch_data[user_id]: del watch_data[user_id]
        save_data()
        await interaction.response.send_message(f"🗑️ 已删除 **{ticker}**")
    else: await interaction.response.send_message(f"❓ 列表里没找到 {ticker}")

@bot.tree.command(name="list", description="查看我的监控列表")
async def list_stocks(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_stocks = watch_data.get(user_id, {})
    if not user_stocks: return await interaction.response.send_message("📭 您的个人列表为空")
    embed = discord.Embed(title=f"📋 {interaction.user.name} 的关注列表", color=discord.Color.blue())
    lines = []
    for ticker, data in user_stocks.items():
        lines.append(f"**{ticker}** ({data['mode']})")
    embed.description = " | ".join(lines)
    embed.set_footer(text="FMP Ultimate API • 机构级多因子模型")
    await interaction.response.send_message(embed=embed)

# ================= 定时任务 =================
ny_tz = pytz.timezone('America/New_York')
target_time = datetime.time(hour=16, minute=1, tzinfo=ny_tz)

@tasks.loop(time=target_time)
async def daily_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    print(f"🔎 启动收盘扫描: {today} (美东 16:01)")
    
    for user_id, stocks in watch_data.items():
        user_alerts = []
        for ticker, data in stocks.items():
            try:
                price, signals, debug_info = analyze_daily_signals(ticker)
                if signals:
                    score, desc_final = generate_report_content(signals)
                    should_alert = False
                    mode = data['mode']
                    if mode == 'always': should_alert = True
                    if mode == 'once_daily' and data.get('last_alert_date') != today: should_alert = True
                    
                    if should_alert:
                        data['last_alert_date'] = today
                        text_part, color = format_dashboard_title(score)
                        embed = discord.Embed(title=f"{ticker} : {text_part}", description=f"**现价**: ${price:.2f}\n\n{desc_final}", color=color)
                        embed.set_image(url=get_finviz_chart_url(ticker))
                        embed.set_footer(text=f"[DEBUG] {debug_info}")
                        user_alerts.append(embed)
            except Exception as e: print(f"Error {ticker}: {e}")
        if user_alerts:
            save_data()
            await channel.send(f"🔔 <@{user_id}> 您的 **{today}** 收盘日报已送达:")
            for embed in user_alerts:
                await channel.send(embed=embed)
                await asyncio.sleep(1)
            await channel.send("---")

bot.run(TOKEN)
