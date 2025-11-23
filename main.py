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

# ================= 配置区域 =================
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') 

BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist.json")
CONFIG_FILE = os.path.join(BASE_PATH, "config.json")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

watch_data = {}
bot_config = {"interval": 30}

# ================= 数据存取 =================
def load_data():
    global watch_data, bot_config
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                watch_data = json.load(f)
            print(f"📚 已从 {DATA_FILE} 加载 {len(watch_data)} 个目标")
        except: watch_data = {}
            
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                bot_config = json.load(f)
            print(f"⚙️ 已加载配置: 间隔 {bot_config.get('interval')} 分钟")
        except: bot_config = {"interval": 30}
    
    if not watch_data:
        default_tickers = ["TSLA", "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META"]
        print("⚡ 初始化默认列表: 七大科技")
        for t in default_tickers:
            watch_data[t] = {"mode": "once_daily", "last_alert_date": "", "last_signals": []}
        save_data()

def save_data():
    try:
        with open(DATA_FILE, 'w') as f: json.dump(watch_data, f, indent=4)
    except Exception as e: print(f"❌ 保存列表失败: {e}")

def save_config():
    try:
        with open(CONFIG_FILE, 'w') as f: json.dump(bot_config, f, indent=4)
    except Exception as e: print(f"❌ 保存配置失败: {e}")

# ================= ⚖️ 评分系统 (V3.2) =================
def get_signal_score(s):
    if "Nx 突破" in s or "Nx 牛市" in s: return 4
    if "Nx 跌破" in s or "Nx 熊市" in s: return -4
    
    bull_lv3 = ["放量大涨", "盘中爆量抢筹", "多头排列", "突破年线", "OBV 底背离", "突破唐奇安"]
    bear_lv3 = ["放量大跌", "盘中爆量杀跌", "空头排列", "跌破年线", "断头铡刀", "OBV 顶背离", "跌破唐奇安"]
    if any(x in s for x in bull_lv3): return 3
    if any(x in s for x in bear_lv3): return -3
    
    bull_lv2 = ["Nx 站稳", "金叉", "突破布林", "ADX", "阳包阴", "早晨之星", "锤子", "底背离"]
    bear_lv2 = ["死叉", "跌破布林", "阴包阳", "射击之星", "顶背离"]
    if any(x in s for x in bull_lv2): return 2
    if any(x in s for x in bear_lv2): return -2
    
    bull_lv1 = ["站上", "突破 R1", "超卖", "触底", "回升", "KDJ 低位"]
    bear_lv1 = ["跌破", "跌破 S1", "超买", "见顶", "滞涨", "缩量"]
    if any(x in s for x in bull_lv1): return 1
    if any(x in s for x in bear_lv1): return -1
    
    return 0

def calculate_total_score(signals):
    total = 0
    for s in signals:
        total += get_signal_score(s)
    return total

def format_dashboard_title(score):
    count = int(min(abs(score), 8))
    
    icons = ""
    if score > 0: icons = "⭐" * count
    elif score < 0: icons = "💀" * count
    else: icons = "⚖️"
    
    status = "震荡"
    color = discord.Color.light_grey()
    
    if score >= 8:
        status = "史诗暴涨"
        color = discord.Color.from_rgb(255, 0, 0)
    elif score >= 4:
        status = "极度强势"
        color = discord.Color.red()
    elif score >= 1:
        status = "趋势看多"
        color = discord.Color.orange()
    elif score <= -8:
        status = "史诗崩盘"
        color = discord.Color.from_rgb(0, 255, 0)
    elif score <= -4:
        status = "极度高危"
        color = discord.Color.green()
    elif score <= -1:
        status = "趋势看空"
        color = discord.Color.dark_teal()
    else:
        status = "震荡整理"
        color = discord.Color.gold()
        
    text_part = f"{status} ({score:+}) {icons}"
    return text_part, color

# ================= 🧠 战法说明书 =================
def get_signal_advice(signal_text):
    t = signal_text
    advice = ""
    
    if "Nx 突破" in t: advice = "Nx买入: 突破蓝黄双梯，满仓进攻信号。"
    elif "Nx 跌破" in t: advice = "Nx逃命: 跌破短期生命线，必须减仓或清仓。"
    elif "Nx 站稳" in t: advice = "Nx持股: 收盘价在蓝梯之上，不破下沿死不卖。"
    elif "Nx 牛市" in t: advice = "Nx趋势: 蓝梯在黄梯之上，大周期看涨。"
    elif "Nx 熊市" in t: advice = "Nx趋势: 蓝梯在黄梯之下，反弹即是空。"

    elif "盘中爆量" in t: advice = "资金异动: 盘中资金大举进出，日内方向可信。"
    elif "放量" in t: advice = "量价配合: 趋势有资金支持，右侧交易。"
    elif "OBV" in t: advice = "聪明钱: 资金流向背离，信资金。"
    elif "缩量" in t: advice = "洗盘: 交易清淡，卖盘枯竭，关注变盘。"

    elif "多头排列" in t: advice = "最强趋势: 均线全线发散向上，坚定持股。"
    elif "空头排列" in t: advice = "最弱趋势: 均线全线发散向下，空仓观望。"
    elif "年线" in t: advice = "牛熊分界: 200日均线是长线资金的生命线。"
    elif "站上" in t: advice = "均线突破: 站上关键支撑，短线看多。"
    elif "跌破" in t: advice = "均线破位: 跌穿关键支撑，短线看空。"
    elif "ADX" in t: advice = "趋势加速: 结束震荡，单边暴力行情开启。"

    elif "断头" in t or "阴包阳" in t or "射击" in t: advice = "顶部形态: 强烈见顶信号，立即离场。"
    elif "早晨" in t or "锤子" in t or "阳包阴" in t: advice = "底部形态: 强烈见底信号，尝试抄底。"
    elif "背离" in t: advice = "动能衰竭: 价格新高/低但指标不跟，反转在即。"
    elif "布林" in t: advice = "轨道交易: 突破/跌破布林轨道，注意变盘。"
    elif "波动率" in t: advice = "变盘预警: 盘面静极思动，大行情即将出现。"

    elif "KDJ" in t: advice = "短线摆动: 随机指标金/死叉，适合箱体操作。"
    elif "威廉" in t or "WR" in t: advice = "极值交易: 威廉指标触底/见顶，灵敏度极高。"
    elif "CCI" in t: advice = "超跌反弹: 暴跌后的第一波修复，快进快出。"
    elif "超买" in t: advice = "情绪过热: 获利盘随时可能兑现。"
    elif "超卖" in t: advice = "情绪冰点: 恐慌盘杀出，关注反弹。"
    
    elif "S1" in t or "R1" in t: advice = "关键位: 斐波那契点位突破/跌破。"
    elif "唐奇安" in t: advice = "海龟交易: 突破/跌破20日极值。"

    return advice

# ================= FMP Stable 接口 =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_daily_data_stable(ticker):
    if not FMP_API_KEY: return None
    try:
        hist_url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={ticker}&apikey={FMP_API_KEY}"
        hist_resp = requests.get(hist_url, timeout=10)
        if hist_resp.status_code != 200: return None
        hist_data = hist_resp.json()
        if not hist_data: return None
        
        df = pd.DataFrame(hist_data)
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df = df.iloc[::-1].reset_index(drop=True)
        
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
            new_row = {
                'date': today_str, 
                'open': curr.get('open', df['close'].iloc[-1]),
                'high': curr.get('dayHigh', curr['price']), 
                'low': curr.get('dayLow', curr['price']),
                'close': curr['price'], 
                'volume': curr.get('volume', 0)
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        print(f"❌ 数据处理异常 {ticker}: {e}")
        return None

def get_col(df, prefix):
    for col in df.columns:
        if str(col).startswith(prefix): return col
    return None

def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 250: return None, None

    signals = []
    
    # --- 计算指标 ---
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
    df.ta.willr(length=14, append=True) 
    df.ta.cci(length=20, append=True)
    df.ta.adx(length=14, append=True)   
    df.ta.obv(append=True)
    df.ta.atr(length=14, append=True)
    df.ta.donchian(lower_length=20, upper_length=20, append=True)
    try: df.ta.pivots(type="fibonacci", append=True)
    except: pass
    
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)

    # ⚡️ 强制列名大写
    df.columns = [str(c).upper() for c in df.columns]

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    price = curr['CLOSE']

    # === 🛡️ 动态列名查找 (健壮性核心) ===
    col_bbu = get_col(df, 'BBU')
    col_bbl = get_col(df, 'BBL')
    col_bbm = get_col(df, 'BBM')
    col_macd = get_col(df, 'MACD_')
    col_sig = get_col(df, 'MACDS_')
    col_atr = get_col(df, 'ATRR') or get_col(df, 'ATR') # 重点修复: 尝试找 ATRR 或 ATR
    col_adx = get_col(df, 'ADX')
    col_k = get_col(df, 'K_')
    col_d = get_col(df, 'D_')
    col_wr = get_col(df, 'WILLR')
    col_cci = get_col(df, 'CCI')

    # --- Nx ---
    is_break_blue = prev['CLOSE'] < prev['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_BLUE_UP']
    if curr['CLOSE'] > curr['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_YELL_UP']:
        if is_break_blue: signals.append("Nx 突破双梯 (强力买入)")
        elif curr['CLOSE'] > curr['NX_BLUE_DW']: signals.append("Nx 站稳蓝梯 (持股待涨)")
    if prev['CLOSE'] > prev['NX_BLUE_DW'] and curr['CLOSE'] < curr['NX_BLUE_DW']: signals.append(f"Nx 跌破蓝梯下沿")
    if curr['NX_BLUE_DW'] > curr['NX_YELL_UP']: signals.append("Nx 牛市排列")
    elif curr['NX_YELL_DW'] > curr['NX_BLUE_UP']: signals.append("Nx 熊市压制")

    # --- Vol ---
    vol_ma = curr['VOL_MA_20']
    if pd.notna(vol_ma) and vol_ma > 0:
        rvol = curr['VOLUME'] / vol_ma
        if rvol > 2.0 and curr['CLOSE'] > prev['CLOSE']: signals.append(f"放量大涨 (量比:{rvol:.1f}x)")
        elif rvol > 2.0 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"放量大跌 (量比:{rvol:.1f}x)")
        elif rvol < 0.6 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"缩量回调 (量比:{rvol:.1f}x)")

    # --- MA ---
    if (curr['SMA_5'] > curr['SMA_10'] > curr['SMA_20'] > curr['SMA_60']): signals.append("均线多头排列")
    if (curr['SMA_5'] < curr['SMA_10'] < curr['SMA_20'] < curr['SMA_60']): signals.append("均线空头排列")
    
    for m in mas:
        if m == 30: continue
        ma_col = f'SMA_{m}'
        if ma_col in df.columns:
            if prev['CLOSE'] < prev[ma_col] and curr['CLOSE'] > curr[ma_col]:
                if m == 200: signals.append(f"🐂 突破年线 MA200")
                else: signals.append(f"站上 MA{m}")
            elif prev['CLOSE'] > prev[ma_col] and curr['CLOSE'] < curr[ma_col]:
                if m == 200: signals.append(f"🐻 跌破年线 MA200")
                else: signals.append(f"跌破 MA{m}")

    # --- Osc ---
    if col_bbu and curr['CLOSE'] > curr[col_bbu]: signals.append(f"突破布林上轨")
    if col_bbl and curr['CLOSE'] < curr[col_bbl]: signals.append(f"跌破布林下轨")
    if col_bbm:
        if prev['CLOSE'] < prev[col_bbm] and curr['CLOSE'] > curr[col_bbm]: signals.append("🛫 突破布林中轨")
        elif prev['CLOSE'] > prev[col_bbm] and curr['CLOSE'] < curr[col_bbm]: signals.append("📉 跌破布林中轨")

    if col_atr and curr[col_atr] > prev[col_atr] * 1.1: signals.append("🌊 波动率爆发")

    if curr['RSI_14'] > 75: signals.append(f"RSI 超买 ({curr['RSI_14']:.1f})")
    elif curr['RSI_14'] < 30: signals.append(f"RSI 超卖 ({curr['RSI_14']:.1f})")

    if col_macd and col_sig:
        if prev[col_macd] < prev[col_sig] and curr[col_macd] > curr[col_sig]: signals.append("MACD 金叉")
        elif prev[col_macd] > prev[col_sig] and curr[col_macd] < curr[col_sig]: signals.append("MACD 死叉")

    window = 20
    recent = df.iloc[-window:-1]
    if not recent.empty:
        if curr['CLOSE'] > recent['CLOSE'].max() and curr['RSI_14'] < recent['RSI_14'].max(): signals.append("RSI 顶背离")
        if curr['CLOSE'] < recent['CLOSE'].min() and curr['RSI_14'] > recent['RSI_14'].min(): signals.append("RSI 底背离")
        if col_macd:
            if curr['CLOSE'] > recent['CLOSE'].max() and curr[col_macd] < recent[col_macd].max(): signals.append("MACD 顶背离")
            if curr['CLOSE'] < recent['CLOSE'].min() and curr[col_macd] > recent[macd].min(): signals.append("MACD 底背离")
        if curr['CLOSE'] < recent['CLOSE'].min() and curr['OBV'] > recent['OBV'].min(): signals.append("OBV 底背离")
        if curr['CLOSE'] > recent['CLOSE'].max() and curr['OBV'] < recent['OBV'].max(): signals.append("OBV 顶背离")

    if col_k and col_d:
        if prev[col_k] < prev[col_d] and curr[col_k] > curr[col_d] and curr[col_k] < 30: signals.append(f"KDJ 低位金叉")
    
    if col_wr:
        if prev[col_wr] < -80 and curr[col_wr] > -80: signals.append(f"威廉指标 WR 触底 ({curr[col_wr]:.1f})")
        if prev[col_wr] > -20 and curr[col_wr] < -20: signals.append(f"威廉指标 WR 见顶 ({curr[col_wr]:.1f})")
    
    if col_cci:
        if prev[col_cci] < -100 and curr[col_cci] > -100: signals.append(f"CCI 超卖回升 ({curr[col_cci]:.1f})")
    
    if col_adx and prev[col_adx] < 25 and curr[col_adx] > 25: signals.append(f"ADX 趋势加速 ({curr[col_adx]:.1f})")

    ma_short = ['NX_BLUE_UP', 'NX_BLUE_DW']
    if all(curr['OPEN'] > curr[m] for m in ma_short) and all(curr['CLOSE'] < curr[m] for m in ma_short):
        signals.append("断头铡刀")
    
    body = abs(curr['CLOSE'] - curr['OPEN'])
    lower_shadow = min(curr['CLOSE'], curr['OPEN']) - curr['LOW']
    if body > 0 and lower_shadow > (body * 2) and curr['RSI_14'] < 50: signals.append("锤子线")

    is_red_prev = prev['CLOSE'] < prev['OPEN']
    is_green_curr = curr['CLOSE'] > curr['OPEN']
    if is_red_prev and is_green_curr and curr['OPEN'] < prev['CLOSE'] and curr['CLOSE'] > prev['OPEN']: signals.append("阳包阴")
    if not is_red_prev and not is_green_curr and curr['OPEN'] > prev['CLOSE'] and curr['CLOSE'] < prev['OPEN']: signals.append("阴包阳")

    return price, signals

# ================= Bot 指令集 =================

@bot.event
async def on_ready():
    load_data()
    print(f'✅ 健壮修复版Bot已启动: {bot.user}')
    
    interval = bot_config.get('interval', 30)
    daily_monitor.change_interval(minutes=interval)
    await bot.tree.sync()
    if not daily_monitor.is_running(): daily_monitor.start()

@bot.tree.command(name="help_bot", description="显示指令手册")
async def help_bot(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 指令手册", color=discord.Color.blue())
    embed.add_field(name="⚙️ 设置", value="`/set_interval [分钟]` : 修改扫描频率", inline=False)
    embed.add_field(name="📋 监控", value="`/add [代码] [模式]` : 添加股票\n`/remove [代码]` : 删除股票\n`/list` : 查看列表", inline=False)
    embed.add_field(name="🔎 临时查询", value="`/check [代码]` : 立刻分析股票", inline=False)
    embed.add_field(name="📚 战法", value="`/alert_types` : 查看技术指标说明", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="set_interval", description="设置扫描间隔")
async def set_interval(interaction: discord.Interaction, minutes: int):
    if minutes < 1: return await interaction.response.send_message("❌ 间隔不能小于1分钟")
    bot_config['interval'] = minutes
    save_config()
    daily_monitor.change_interval(minutes=minutes)
    await interaction.response.send_message(f"✅ 间隔已更新为: {minutes} 分钟")

@bot.tree.command(name="check", description="立刻分析多只股票 (空格分隔)")
@app_commands.describe(tickers="股票代码列表 (例如: TSLA NVDA AAPL)")
async def check_stocks(interaction: discord.Interaction, tickers: str):
    await interaction.response.defer()
    stock_list = tickers.upper().replace(',', ' ').split()
    if len(stock_list) > 5: await interaction.followup.send("⚠️ 一次最多查询 5 只股票，请分批查询。")
    stock_list = stock_list[:5]

    for ticker in stock_list:
        price, signals = analyze_daily_signals(ticker)
        if price is None:
            await interaction.followup.send(f"❌ 无法获取 {ticker} 数据")
            continue
        if not signals: signals.append("趋势平稳，暂无异动")

        score = calculate_total_score(signals)
        text_part, color = format_dashboard_title(score)
        
        desc_lines = []
        for s in signals:
            kw = s.split("(")[0].strip()
            if "MA" in s: kw = s.split("(")[0].strip()
            advice = get_signal_advice(kw)
            s_score = get_signal_score(s)
            score_display = f"(+{s_score})" if s_score > 0 else f"({s_score})"
            if s_score == 0: score_display = ""
            desc_lines.append(f"### {s} {score_display}")
            if advice: desc_lines.append(f"> {advice}\n")
        
        desc_final = "\n".join(desc_lines)

        embed = discord.Embed(
            title=f"{ticker} : {text_part}",
            description=f"**现价**: ${price:.2f}\n\n{desc_final}",
            color=color
        )
        embed.set_image(url=get_finviz_chart_url(ticker))
        embed.timestamp = datetime.datetime.now()
        embed.set_footer(text="FMP Stable API • 盘中实时分析")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="add", description="添加监控")
@app_commands.choices(mode=[app_commands.Choice(name="每日一次", value="once_daily"), app_commands.Choice(name="总是提醒", value="always")])
async def add_stock(interaction: discord.Interaction, ticker: str, mode: str = "once_daily"):
    ticker = ticker.upper()
    watch_data[ticker] = {"mode": mode, "last_alert_date": "", "last_signals": []}
    save_data()
    await interaction.response.send_message(f"✅ 已添加 **{ticker}**")

@bot.tree.command(name="remove", description="删除监控")
async def remove_stock(interaction: discord.Interaction, ticker: str):
    ticker = ticker.upper()
    if ticker in watch_data:
        del watch_data[ticker]
        save_data()
        await interaction.response.send_message(f"🗑️ 已删除 **{ticker}**")
    else: await interaction.response.send_message(f"❓ 找不到 {ticker}")

@bot.tree.command(name="list", description="查看监控列表")
async def list_stocks(interaction: discord.Interaction):
    if not watch_data: return await interaction.response.send_message("📭 列表为空")
    embed = discord.Embed(title="📋 监控面板", color=discord.Color.blue())
    lines = []
    for ticker, data in watch_data.items():
        sigs = data.get('last_signals', [])
        score = calculate_total_score(sigs)
        text_part, _ = format_dashboard_title(score)
        current_sig_str = " | ".join(sigs) if sigs else "等待扫描..."
        lines.append(f"**{ticker}** : {text_part}\n└ {current_sig_str}")
    embed.description = "\n\n".join(lines)[:4000]
    await interaction.response.send_message(embed=embed)

@tasks.loop(minutes=30)
async def daily_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel or not watch_data: return
    print(f"🔎 扫描... {datetime.datetime.now()}")
    for ticker in list(watch_data.keys()):
        try:
            price, signals = analyze_daily_signals(ticker)
            watch_data[ticker]['last_signals'] = signals if signals else ["趋势平稳"]
            
            should_alert = False
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            mode = watch_data[ticker]['mode']
            
            if signals:
                if mode == 'always': should_alert = True
                elif mode == 'once_daily' and watch_data[ticker].get('last_alert_date') != today: should_alert = True
            
            if should_alert:
                watch_data[ticker]['last_alert_date'] = today
                save_data()
                
                score = calculate_total_score(signals)
                text_part, color = format_dashboard_title(score)
                
                desc_lines = []
                for s in signals:
                    kw = s.split("(")[0].strip()
                    if "MA" in s: kw = s.split("(")[0].strip()
                    advice = get_signal_advice(kw)
                    s_score = get_signal_score(s)
                    score_display = f"(+{s_score})" if s_score > 0 else f"({s_score})"
                    if s_score == 0: score_display = ""
                    desc_lines.append(f"### {s} {score_display}")
                    if advice: desc_lines.append(f"> {advice}\n")
                desc_final = "\n".join(desc_lines)

                embed = discord.Embed(
                    title=f"{ticker} : {text_part}",
                    description=f"**现价**: ${price:.2f}\n\n{desc_final}",
                    color=color
                )
                embed.set_image(url=get_finviz_chart_url(ticker))
                embed.timestamp = datetime.datetime.now()
                embed.set_footer(text="FMP Stable API • Nx战法")
                await channel.send(embed=embed)
                await asyncio.sleep(2)
        except Exception as e:
            print(f"Error {ticker}: {e}")

bot.run(TOKEN)
