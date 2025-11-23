import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests
import pandas as pd
import pandas_ta as ta
import datetime
import os
import asyncio

# ================= 配置区域 =================
# 请确保在 Railway Variables 中设置了这些环境变量
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') 

# 监控列表
WATCHLIST = ['TSLA', 'NVDA', 'AAPL', 'AMD', 'MSFT', 'COIN', 'MSTR', 'GOOGL', 'AMZN', 'META']

# 冷却缓存 (防止刷屏)
alert_cooldown = {}

# 初始化 Bot (注意这里不需要 command_prefix 了，因为我们主用 Slash Command)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ================= 数据与计算逻辑 =================

def get_finviz_chart_url(ticker):
    """ 生成 Finviz 图表链接作为配图 """
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_fmp_data(ticker):
    """ 从 FMP 获取 15分钟 K线数据 """
    url = f"https://financialmodelingprep.com/api/v3/historical-chart/15min/{ticker}?apikey={FMP_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if not data or not isinstance(data, list):
            return None
        
        # FMP 返回的是倒序(最新在前)，Pandas计算指标需要正序(最旧在前)
        df = pd.DataFrame(data)
        df = df.iloc[::-1].reset_index(drop=True) 
        return df
    except Exception as e:
        print(f"❌ 数据获取失败 {ticker}: {e}")
        return None

def calculate_signals(ticker):
    """ 核心量化逻辑：计算所有指标并返回信号 """
    df = get_fmp_data(ticker)
    if df is None or len(df) < 300: 
        return None, ["数据不足或获取失败"]

    signals = []
    price = df['close'].iloc[-1]
    
    # --- 1. 计算指标 (Pandas TA) ---
    # 均线组
    mas = [5, 10, 20, 60, 120, 250]
    for m in mas:
        df.ta.sma(length=m, append=True)
    
    # 布林带 (20, 2)
    df.ta.bbands(length=20, std=2, append=True)
    
    # MACD (12, 26, 9)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    
    # KDJ (9, 3)
    df.ta.kdj(length=9, signal=3, append=True)
    
    # RSI (14)
    df.ta.rsi(length=14, append=True)

    # 成交量均线
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)

    # --- 2. 信号判断 ---
    # 获取最后两行 (curr=当前, prev=上一根)
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # A. 均线系统
    for m in mas:
        ma_col = f'SMA_{m}'
        if ma_col in df.columns:
            if prev['close'] < prev[ma_col] and curr['close'] > curr[ma_col]:
                signals.append(f"📈 突破 MA{m}")
            elif prev['close'] > prev[ma_col] and curr['close'] < curr[ma_col]:
                signals.append(f"📉 跌破 MA{m}")

    # B. 布林带
    bbu = 'BBU_20_2.0'
    bbl = 'BBL_20_2.0'
    if curr['close'] > curr[bbu] and prev['close'] <= prev[bbu]:
        signals.append("🚀 突破布林上轨")
    elif curr['close'] < curr[bbl] and prev['close'] >= prev[bbl]:
        signals.append("🩸 跌破布林下轨")

    # C. MACD
    macd_line = 'MACD_12_26_9'
    signal_line = 'MACDs_12_26_9'
    
    # 金叉
    if prev[macd_line] < prev[signal_line] and curr[macd_line] > curr[signal_line]:
        signals.append("✨ MACD 金叉")
    # 死叉
    if prev[macd_line] > prev[signal_line] and curr[macd_line] < curr[signal_line]:
        signals.append("💀 MACD 死叉")
    # 顶背离 (简化: 价格新高但MACD没新高) - 略过复杂逻辑，保留基础交叉

    # D. RSI (超买超卖 + 简单的数值判断)
    rsi_val = curr['RSI_14']
    if rsi_val > 75:
        signals.append(f"⚠️ RSI 超买 ({rsi_val:.1f})")
    elif rsi_val < 25:
        signals.append(f"💎 RSI 超卖 ({rsi_val:.1f})")
    
    # E. KDJ
    k, d = curr['K_9_3'], curr['D_9_3']
    prev_k, prev_d = prev['K_9_3'], prev['D_9_3']
    if prev_k < prev_d and k > d and k < 20:
        signals.append("⚡ KDJ 低位金叉")
    
    # F. 量能
    if curr['volume'] > (curr['VOL_MA_20'] * 2.5): # 2.5倍放量
        if curr['close'] > prev['close']:
            signals.append("🔥 巨量拉升")
        else:
            signals.append("😰 巨量砸盘")

    return price, signals

# ================= Bot 事件与指令 =================

@bot.event
async def on_ready():
    print(f'✅ 已登录: {bot.user} (ID: {bot.user.id})')
    
    # 关键步骤：同步 Slash Commands 到 Discord 服务器
    try:
        synced = await bot.tree.sync()
        print(f'✅ 已同步 {len(synced)} 个斜杠命令')
    except Exception as e:
        print(f'❌ 同步命令失败: {e}')

    if not scanner_task.is_running():
        print("⏰ 启动定时监控任务...")
        scanner_task.start()

# --- 新增：斜杠指令 /test_signal ---
# 在 Discord 输入 /test_signal 后，按 Tab 键输入股票代码
@bot.tree.command(name="test_signal", description="[测试] 立即分析一只股票的技术指标")
@app_commands.describe(ticker="股票代码 (例如 TSLA)")
async def test_signal(interaction: discord.Interaction, ticker: str):
    # 告诉用户我们在处理，防止超时
    await interaction.response.defer()
    
    ticker = ticker.upper()
    price, signals = calculate_signals(ticker)
    
    if price is None:
        await interaction.followup.send(f"❌ 无法获取 {ticker} 的数据，请检查 FMP Key 或代码是否正确。")
        return

    if not signals:
        signals.append("平淡无奇，暂无明显信号")

    # 制作 Embed
    desc = "\n".join([f"• {s}" for s in signals])
    color = discord.Color.green() if "突破" in desc or "金叉" in desc else discord.Color.gold()
    
    embed = discord.Embed(
        title=f"🔍 手动分析: {ticker}",
        description=f"**现价**: ${price:.2f}\n\n**当前信号**:\n{desc}",
        color=color
    )
    embed.set_image(url=get_finviz_chart_url(ticker))
    embed.set_footer(text="基于 FMP 15分钟数据 • 立即生成")
    
    await interaction.followup.send(embed=embed)

# --- 定时任务 ---
@tasks.loop(minutes=15)
async def scanner_task():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    
    print(f"Running scan at {datetime.datetime.now()}")
    
    for ticker in WATCHLIST:
        try:
            price, signals = calculate_signals(ticker)
            if signals:
                # 冷却检查 (3小时内不重复报同一只)
                now = datetime.datetime.now()
                if ticker in alert_cooldown:
                    if (now - alert_cooldown[ticker]).total_seconds() < 3 * 3600:
                        continue
                
                alert_cooldown[ticker] = now
                
                desc = "\n".join([f"• {s}" for s in signals])
                color = discord.Color.red() if "跌" in desc or "死叉" in desc else discord.Color.green()
                
                embed = discord.Embed(
                    title=f"⚡ 自动警报: {ticker}",
                    description=f"**现价**: ${price:.2f}\n\n{desc}",
                    color=color
                )
                embed.set_image(url=get_finviz_chart_url(ticker))
                embed.timestamp = now
                
                await channel.send(content=f"👀 {ticker} 出现信号", embed=embed)
                await asyncio.sleep(2) # 防刷屏
                
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")

bot.run(TOKEN)
