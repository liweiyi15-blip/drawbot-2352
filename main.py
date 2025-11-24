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

# === 💾 Railway 持久化路径 ===
BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist_v5.json")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 数据结构: { "USER_ID": { "TSLA": {...}, "NVDA": {...} } }
watch_data = {}

# ================= 数据存取 (用户隔离版) =================
def load_data():
    global watch_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                watch_data = json.load(f)
            count = sum(len(v) for v in watch_data.values())
            print(f"📚 已加载 {len(watch_data)} 位用户的共 {count} 个关注目标")
        except: watch_data = {}
    else:
        watch_data = {}
        save_data()

def save_data():
    try:
        with open(DATA_FILE, 'w') as f: json.dump(watch_data, f, indent=4)
    except Exception as e: print(f"❌ 保存失败: {e}")

# ================= ⚖️ 机构级客观评分系统 (V5.0) =================
def get_signal_category_and_score(s):
    s = s.strip()
    # 1. ⏳ 择时 (Timing)
    if "九转" in s or "十三转" in s:
        if "买入" in s or "底部" in s: return 'timing', 4
        if "卖出" in s or "顶部" in s: return 'timing', -4

    # 2. 💰 资金/量能 (Volume)
    if "盘中爆量" in s: return 'volume', 4 if "抢筹" in s else -4
    if "放量" in s: return 'volume', 3 if "大涨" in s else -3
    if "缩量" in s: return 'volume', 1 if "回调" in s else -1

    # 3. 🕯️ K线形态 (Pattern) - 互斥
    p_bull = ["早晨之星", "阳包阴", "锤子"]
    p_bear = ["断头铡刀", "阴包阳", "射击之星", "黄昏之星", "墓碑"]
    if any(x in s for x in p_bull): return 'pattern', 4
    if any(x in s for x in p_bear): return 'pattern', -4

    # 4. 📈 趋势/结构 (Trend) - 互斥
    t_bull_3 = ["多头排列", "突破年线", "突破唐奇安"]
    t_bear_3 = ["空头排列", "跌破年线", "跌破唐奇安"]
    t_bull_2 = ["Nx 突破", "Nx 站稳", "Nx 牛市", "突破 R1"]
    t_bear_2 = ["Nx 跌破", "Nx 熊市", "跌破 S1"]
    t_bull_1 = ["站上"]
    t_bear_1 = ["跌破"]
    if any(x in s for x in t_bull_3): return 'trend', 3
    if any(x in s for x in t_bear_3): return 'trend', -3
    if any(x in s for x in t_bull_2): return 'trend', 2
    if any(x in s for x in t_bear_2): return 'trend', -2
    if any(x in s for x in t_bull_1): return 'trend', 1
    if any(x in s for x in t_bear_1): return 'trend', -1

    # 5. 🌊 摆动/情绪 (Oscillator) - 互斥
    o_bull_3 = ["底背离"]; o_bear_3 = ["顶背离"]
    o_bull_2 = ["MACD 金叉", "突破布林", "ADX"]; o_bear_2 = ["MACD 死叉", "跌破布林"]
    o_bull_1 = ["超卖", "触底", "回升", "KDJ 低位"]; o_bear_1 = ["超买", "见顶", "滞涨"]
    if any(x in s for x in o_bull_3): return 'oscillator', 3
    if any(x in s for x in o_bear_3): return 'oscillator', -3
    if any(x in s for x in o_bull_2): return 'oscillator', 2
    if any(x in s for x in o_bear_2): return 'oscillator', -2
    if any(x in s for x in o_bull_1): return 'oscillator', 1
    if any(x in s for x in o_bear_1): return 'oscillator', -1
    return 'other', 0

def calculate_total_score(signals):
    scores = {'trend': [], 'pattern': [], 'oscillator': [], 'volume': [], 'timing': []}
    for s in signals:
        cat, score = get_signal_category_and_score(s)
        if cat in scores and score != 0: scores[cat].append(score)
    
    total = 0
    # 同类取最大，异类相加
    if scores['trend']: total += max(scores['trend'], key=abs)
    if scores['pattern']: total += max(scores['pattern'], key=abs)
    if scores['oscillator']: total += max(scores['oscillator'], key=abs)
    if scores['volume']: total += sum(scores['volume'])
    if scores['timing']: total += sum(scores['timing'])
    return total

def get_signal_score(s):
    _, score = get_signal_category_and_score(s)
    return score

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

# ================= FMP Ultimate API =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_daily_data_stable(ticker):
    if not FMP_API_KEY: return None
    try:
        # 使用 v3 endpoint 配合 api key 通常是稳定的
        hist_url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={ticker}&apikey={FMP_API_KEY}"
        hist_resp = requests.get(hist_url, timeout=10)
        
        # 兼容性处理：如果 stable 接口挂了，可以自动回退到 v3 (可选，此处保持原逻辑)
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
                'date': today_str, 'open': curr.get('open', df['close'].iloc[-1]),
                'high': curr.get('dayHigh', curr['price']), 'low': curr.get('dayLow', curr['price']),
                'close': curr['price'], 'volume': curr.get('volume', 0)
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        print(f"❌ 数据处理异常 {ticker}: {e}")
        return None

def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 250: return None, None
    signals = []
    
    # ------------------ 指标计算 ------------------
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
    
    # === 九转/十三转 ===
    df.ta.td_seq(append=True)
    
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.columns = [str(c).upper() for c in df.columns]

    curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]
    price = curr['CLOSE']
    
    col_bbu = 'BBU_20_2.0' in df.columns and 'BBU_20_2.0' or 'BBU_20_2'
    col_bbl = 'BBL_20_2.0' in df.columns and 'BBL_20_2.0' or 'BBL_20_2'

    # ------------------ 信号判定逻辑 ------------------
    # A. Nx
    is_break_blue = prev['CLOSE'] < prev['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_BLUE_UP']
    if curr['CLOSE'] > curr['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_YELL_UP']:
        if is_break_blue: signals.append("Nx 突破双梯")
        elif curr['CLOSE'] > curr['NX_BLUE_DW']: signals.append("Nx 站稳蓝梯")
    if curr['NX_BLUE_DW'] > curr['NX_YELL_UP']: signals.append("Nx 牛市排列")
    elif curr['NX_YELL_DW'] > curr['NX_BLUE_UP']: signals.append("Nx 熊市压制")

    # B. 量能
    vol_ma = curr['VOL_MA_20']
    if pd.notna(vol_ma) and vol_ma > 0:
        rvol = curr['VOLUME'] / vol_ma
        if rvol > 2.0 and curr['CLOSE'] > prev['CLOSE']: signals.append(f"放量大涨 (量比:{rvol:.1f}x)")
        elif rvol > 2.0 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"放量大跌 (量比:{rvol:.1f}x)")
        elif rvol < 0.6 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"缩量回调 (量比:{rvol:.1f}x)")

    # C. 支撑压力 & 均线
    if 'P_FIB_R1' in df.columns and prev['CLOSE'] < curr['P_FIB_R1'] and curr['CLOSE'] > curr['P_FIB_R1']: signals.append(f"突破 R1 阻力")
    if curr['CLOSE'] > prev['DCU_20_20']: signals.append(f"突破唐奇安上轨")
    
    if (curr['SMA_5'] > curr['SMA_10'] > curr['SMA_20'] > curr['SMA_60']): signals.append("均线多头排列")
    if (curr['SMA_5'] < curr['SMA_10'] < curr['SMA_20'] < curr['SMA_60']): signals.append("均线空头排列")
    if prev['CLOSE'] < prev['SMA_200'] and curr['CLOSE'] > curr['SMA_200']: signals.append("🐂 突破年线 MA200")
    if prev['CLOSE'] > prev['SMA_200'] and curr['CLOSE'] < curr['SMA_200']: signals.append("🐻 跌破年线 MA200")

    # D. 震荡
    if curr['RSI_14'] > 75: signals.append(f"RSI 超买 ({curr['RSI_14']:.1f})")
    elif curr['RSI_14'] < 30: signals.append(f"RSI 超卖 ({curr['RSI_14']:.1f})")
    
    # E. K线形态
    body = abs(curr['CLOSE'] - curr['OPEN'])
    lower_shadow = min(curr['CLOSE'], curr['OPEN']) - curr['LOW']
    if body > 0 and lower_shadow > (body * 2) and curr['RSI_14'] < 50: signals.append("锤子线")
    if prev['CLOSE'] < prev['OPEN'] and curr['CLOSE'] > curr['OPEN'] and curr['OPEN'] < prev['CLOSE'] and curr['CLOSE'] > prev['OPEN']: signals.append("阳包阴")

    # F. 九转/十三转 (Setup / Countdown)
    # TD Setup
    col_td_setup = 'TD_SEQ_SETUP'
    if col_td_setup in df.columns and prev[col_td_setup] < 9 and curr[col_td_setup] == 9:
        if curr['CLOSE'] < curr['OPEN']: signals.append("神奇九转: 底部买入信号 (9)")
        else: signals.append("神奇九转: 顶部卖出信号 (9)")
    
    # TD Countdown
    col_td_cd = 'TD_SEQ_CD'
    if col_td_cd in df.columns and prev[col_td_cd] < 13 and curr[col_td_cd] == 13:
        if curr['CLOSE'] < curr['OPEN']: signals.append("迪玛克十三转: 终极底部 (13)")
        else: signals.append("迪玛克十三转: 终极顶部 (13)")

    return price, signals

# ================= Bot 指令集 =================

@bot.event
async def on_ready():
    load_data()
    print(f'✅ V5.2 机构级Bot已启动: {bot.user}')
    # 打印下次执行的本地时间，用于确认时区正确性
    print(f'⏰ 定时任务目标: 美东时间 16:01 (自动处理冬夏令时)')
    await bot.tree.sync()
    if not daily_monitor.is_running(): daily_monitor.start()

@bot.tree.command(name="help_bot", description="显示指令手册")
async def help_bot(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 指令手册 (V5.2 User Isolated)", color=discord.Color.blue())
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
        price, signals = analyze_daily_signals(ticker)
        if price is None:
            await interaction.followup.send(f"❌ 无法获取 {ticker} 数据")
            continue
        if not signals: signals.append("趋势平稳，暂无异动")

        score = calculate_total_score(signals)
        text_part, color = format_dashboard_title(score)
        
        desc_final = "\n".join([f"### {s} ({get_signal_score(s)})" for s in signals])

        embed = discord.Embed(title=f"{ticker} : {text_part}", description=f"**现价**: ${price:.2f}\n\n{desc_final}", color=color)
        embed.set_image(url=get_finviz_chart_url(ticker))
        embed.set_footer(text="FMP Ultimate API • 机构级多因子模型")
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

# ================= 定时任务：智能冬夏令时 =================
# 定义纽约时区
ny_tz = pytz.timezone('America/New_York')

# 设定目标时间: 当地时间 16:01
# discord.py 的 loop 会根据 tzinfo 自动计算下一次运行的 UTC 时间
# 如果纽约进入冬令时，偏移量变了，discord.py 会自动调整，始终保持在纽约时间 16:01 触发
target_time = datetime.time(hour=16, minute=1, tzinfo=ny_tz)

@tasks.loop(time=target_time)
async def daily_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    print(f"🔎 启动收盘扫描: {today} (美东 16:01)")
    
    # 遍历所有用户
    for user_id, stocks in watch_data.items():
        user_alerts = []
        
        for ticker, data in stocks.items():
            try:
                price, signals = analyze_daily_signals(ticker)
                
                if signals:
                    should_alert = False
                    mode = data['mode']
                    
                    if mode == 'always': should_alert = True
                    # V5.0逻辑: Lv4极值信号(九转/爆量)强制推送
                    is_lv4 = any(get_signal_score(s) in [4, -4] for s in signals)
                    if mode == 'once_daily' and data.get('last_alert_date') != today: should_alert = True
                    
                    if should_alert:
                        data['last_alert_date'] = today
                        
                        score = calculate_total_score(signals)
                        text_part, color = format_dashboard_title(score)
                        
                        desc_final = "\n".join([f"### {s} ({get_signal_score(s)})" for s in signals])
                        
                        embed = discord.Embed(title=f"{ticker} : {text_part}", description=f"**现价**: ${price:.2f}\n\n{desc_final}", color=color)
                        embed.set_image(url=get_finviz_chart_url(ticker))
                        embed.set_footer(text="FMP Ultimate API • 机构级多因子模型")
                        
                        user_alerts.append(embed)
            except Exception as e:
                print(f"Error {ticker}: {e}")
        
        # 统一推送
        if user_alerts:
            save_data()
            await channel.send(f"🔔 <@{user_id}> 您的 **{today}** 收盘日报已送达:")
            for embed in user_alerts:
                await channel.send(embed=embed)
                await asyncio.sleep(1)
            await channel.send("---")

bot.run(TOKEN)
