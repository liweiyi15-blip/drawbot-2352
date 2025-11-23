import discord
from discord.ext import commands, tasks
import requests
import pandas as pd
import pandas_ta as ta
import numpy as np
import datetime
import os
import asyncio

# --- 配置 ---
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') # 记得在 Railway 变量里填入你的 FMP Key

# 监控列表
WATCHLIST = ['TSLA', 'NVDA', 'AAPL', 'AMD', 'MSFT', 'COIN', 'MSTR']

# 冷却时间
alert_cooldown = {} 

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# FMP 获取数据的函数
def get_fmp_data(ticker):
    # 获取15分钟级别数据，适合日内波段
    url = f"https://financialmodelingprep.com/api/v3/historical-chart/15min/{ticker}?apikey={FMP_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if not data or not isinstance(data, list):
            return None
        
        # 转为 DataFrame
        df = pd.DataFrame(data)
        # FMP 返回的是倒序的(最新在前)，Pandas计算需要正序(旧->新)
        df = df.iloc[::-1].reset_index(drop=True)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        print(f"FMP Error {ticker}: {e}")
        return None

# --- 核心：全能指标计算引擎 ---
def analyze_market(ticker):
    df = get_fmp_data(ticker)
    if df is None or len(df) < 300: return None, None

    signals = []
    price = df['close'].iloc[-1]
    
    # ----------------------------------------------------
    # 1. 计算所有指标
    # ----------------------------------------------------
    # MA 均线组
    mas = [5, 10, 20, 60, 120, 250]
    for m in mas:
        df.ta.sma(length=m, append=True)
    
    # Bollinger Bands (20, 2)
    df.ta.bbands(length=20, std=2, append=True)
    
    # MACD (12, 26, 9)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    # 列名通常是: MACD_12_26_9, MACDs_12_26_9(信号线), MACDh_12_26_9(柱子)

    # KDJ (9, 3)
    df.ta.kdj(length=9, signal=3, append=True)
    
    # RSI (14)
    df.ta.rsi(length=14, append=True)

    # 成交量均线 (用于判断放量)
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)

    # 获取最后两行数据 (curr=当前, prev=上一根K线) 用于判断交叉
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # ----------------------------------------------------
    # 2. 信号判断逻辑
    # ----------------------------------------------------

    # === A. 均线突破/跌破 (MA 5, 10... 250) ===
    for m in mas:
        ma_col = f'SMA_{m}'
        if ma_col in df.columns:
            # 突破: 上一根在下面，这一根在上面
            if prev['close'] < prev[ma_col] and curr['close'] > curr[ma_col]:
                signals.append(f"📈 突破 MA{m} 均线")
            # 跌破
            elif prev['close'] > prev[ma_col] and curr['close'] < curr[ma_col]:
                signals.append(f"📉 跌破 MA{m} 均线")

    # === B. 布林带逻辑 ===
    bbu = f'BBU_20_2.0' # 上轨
    bbl = f'BBL_20_2.0' # 下轨
    bbm = f'BBM_20_2.0' # 中轨
    
    if curr['close'] > curr[bbu] and prev['close'] <= prev[bbu]:
        signals.append("🚀 突破布林带上轨 (强势)")
    elif curr['close'] < curr[bbl] and prev['close'] >= prev[bbl]:
        signals.append("🩸 跌破布林带下轨 (超卖)")
    
    # === C. MACD 金叉/死叉/排列 ===
    macd = 'MACD_12_26_9'
    signal = 'MACDs_12_26_9'
    hist = 'MACDh_12_26_9'
    
    # 金叉 (快线上穿慢线)
    if prev[macd] < prev[signal] and curr[macd] > curr[signal]:
        signals.append("✨ MACD 金叉")
    # 死叉
    if prev[macd] > prev[signal] and curr[macd] < curr[signal]:
        signals.append("💀 MACD 死叉")
        
    # 多头排列 (MACD > Signal > 0)
    if curr[macd] > curr[signal] and curr[signal] > 0:
        # 这里的逻辑可以更复杂，比如连续3根都在0轴上
        pass # 暂时不报警，因为这是个状态不是瞬间动作，否则一直响

    # === D. 量价分析 (放量/缩量) ===
    # 定义放量：当前成交量 > 2倍的20周期平均量
    is_huge_vol = curr['volume'] > (curr['VOL_MA_20'] * 2.0)
    is_price_up = curr['close'] > prev['close']
    
    if is_huge_vol and is_price_up:
        signals.append("🔥 放量上涨 (主力进场)")
    elif is_huge_vol and not is_price_up:
        signals.append("😰 放量下跌 (恐慌抛售)")

    # === E. KDJ 指标 ===
    k = curr['K_9_3']
    d = curr['D_9_3']
    prev_k = prev['K_9_3']
    prev_d = prev['D_9_3']
    
    if prev_k < prev_d and k > d and k < 20:
        signals.append("💎 KDJ 低位金叉")
    elif prev_k > prev_d and k < d and k > 80:
        signals.append("⚠️ KDJ 高位死叉")

    # === F. RSI 背离 (简化版) ===
    # 背离很难写完美，这里用简化逻辑：
    # 顶背离：价格创新高 (最近20根)，但 RSI 没创新高
    rsi_col = 'RSI_14'
    window = 20
    recent = df.iloc[-window:]
    
    # 顶背离判断
    price_high = recent['close'].max()
    rsi_high = recent[rsi_col].max()
    
    # 如果当前价格接近最高价，但当前 RSI 远低于最高 RSI
    if (curr['close'] >= price_high * 0.995) and (curr[rsi_col] < rsi_high * 0.85):
        signals.append("📉 RSI 顶背离警报 (价格新高指标未跟)")

    # 底背离判断
    price_low = recent['close'].min()
    rsi_low = recent[rsi_col].min()
    
    if (curr['close'] <= price_low * 1.005) and (curr[rsi_col] > rsi_low * 1.15):
        signals.append("📈 RSI 底背离警报 (价格新低指标回升)")

    return price, signals

# --- 任务循环 ---
@tasks.loop(minutes=15)
async def technical_scanner():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    
    for ticker in WATCHLIST:
        try:
            price, signals = analyze_market(ticker)
            if signals:
                # 冷却检查...
                now = datetime.datetime.now()
                if ticker in alert_cooldown:
                     if (now - alert_cooldown[ticker]).total_seconds() < 3600: # 1小时冷却
                         continue
                alert_cooldown[ticker] = now
                
                # 发送
                desc = "\n".join([f"• {s}" for s in signals])
                color = discord.Color.green() if "突破" in desc or "金叉" in desc else discord.Color.red()
                
                embed = discord.Embed(title=f"⚡ {ticker} 技术信号触发", description=f"**现价**: ${price}\n\n{desc}", color=color)
                embed.set_footer(text="数据源: FMP付费版 • 15分钟周期")
                await channel.send(embed=embed)
                await asyncio.sleep(1)
        except Exception as e:
            print(f"扫描错误: {e}")

@bot.event
async def on_ready():
    print(f"FMP Bot 已启动: {bot.user}")
    technical_scanner.start()

bot.run(TOKEN)
