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
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') 

# 监控列表
WATCHLIST = ['TSLA', 'NVDA', 'AAPL', 'AMD', 'MSFT', 'COIN', 'MSTR', 'GOOGL', 'AMZN', 'META']

alert_cooldown = {}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ================= 数据与计算逻辑 =================

def get_finviz_chart_url(ticker):
    """ 生成 Finviz 图表链接作为配图 """
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_fmp_data(ticker):
    """ 
    从 FMP 获取 15分钟 K线数据 (适配新版 /stable/ 接口)
    """
    if not FMP_API_KEY:
        print(f"❌ 错误: 未检测到 FMP_API_KEY 环境变量！")
        return None
        
    # --- 关键修改：使用新的 /stable/ 接口结构 ---
    # 旧版: /api/v3/historical-chart/15min/{ticker}
    # 新版: /stable/historical-chart/15min?symbol={ticker}
    url = f"https://financialmodelingprep.com/stable/historical-chart/15min?symbol={ticker}&apikey={FMP_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"❌ FMP API 报错 {ticker}: 状态码 {response.status_code} - {response.text}")
            return None
            
        data = response.json()
        if not data:
            print(f"⚠️ FMP 返回空数据 {ticker} (可能是非交易时间或代码错误)")
            return None
        
        # 检查是否包含错误信息
        if isinstance(data, dict) and "Error Message" in data:
            print(f"❌ FMP 权限错误: {data['Error Message']}")
            return None
            
        # 转换为 DataFrame
        df = pd.DataFrame(data)
        
        # FMP 新版接口返回的数据通常也是倒序的(最新在前)，需要反转
        df = df.iloc[::-1].reset_index(drop=True) 
        
        # 确保列名统一 (FMP 返回的是 date, open, high, low, close, volume)
        # pandas_ta通常能自动识别，但为了保险起见，不强制重命名，除非列名不对
        
        return df
    except Exception as e:
        print(f"❌ 数据获取异常 {ticker}: {e}")
        return None

def calculate_signals(ticker):
    """ 核心量化逻辑：计算所有指标并返回信号 """
    df = get_fmp_data(ticker)
    
    # 判空保护
    if df is None or len(df) < 50: 
        return None, None

    signals = []
    try:
        price = df['close'].iloc[-1]
        
        # --- 1. 计算指标 (Pandas TA) ---
        mas = [5, 10, 20, 60, 120, 250]
        for m in mas:
            df.ta.sma(length=m, append=True)
        
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.kdj(length=9, signal=3, append=True)
        df.ta.rsi(length=14, append=True)
        df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)

        # --- 2. 信号判断 ---
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # A. 均线系统
        for m in mas:
            ma_col = f'SMA_{m}'
            if ma_col in df.columns:
                if pd.notna(prev[ma_col]) and pd.notna(curr[ma_col]):
                    if prev['close'] < prev[ma_col] and curr['close'] > curr[ma_col]:
                        signals.append(f"📈 突破 MA{m}")
                    elif prev['close'] > prev[ma_col] and curr['close'] < curr[ma_col]:
                        signals.append(f"📉 跌破 MA{m}")

        # B. 布林带
        bbu = 'BBU_20_2.0'
        bbl = 'BBL_20_2.0'
        if bbu in df.columns and bbl in df.columns:
            if curr['close'] > curr[bbu] and prev['close'] <= prev[bbu]:
                signals.append("🚀 突破布林上轨")
            elif curr['close'] < curr[bbl] and prev['close'] >= prev[bbl]:
                signals.append("🩸 跌破布林下轨")

        # C. MACD
        macd_line = 'MACD_12_26_9'
        signal_line = 'MACDs_12_26_9'
        if macd_line in df.columns and signal_line in df.columns:
            if prev[macd_line] < prev[signal_line] and curr[macd_line] > curr[signal_line]:
                signals.append("✨ MACD 金叉")
            if prev[macd_line] > prev[signal_line] and curr[macd_line] < curr[signal_line]:
                signals.append("💀 MACD 死叉")

        # D. RSI
        if 'RSI_14' in df.columns:
            rsi_val = curr['RSI_14']
            if rsi_val > 75:
                signals.append(f"⚠️ RSI 超买 ({rsi_val:.1f})")
            elif rsi_val < 25:
                signals.append(f"💎 RSI 超卖 ({rsi_val:.1f})")
        
        # E. KDJ
        if 'K_9_3' in df.columns and 'D_9_3' in df.columns:
            k, d = curr['K_9_3'], curr['D_9_3']
            prev_k, prev_d = prev['K_9_3'], prev['D_9_3']
            if prev_k < prev_d and k > d and k < 20:
                signals.append("⚡ KDJ 低位金叉")
        
        # F. 量能
        if 'VOL_MA_20' in df.columns and pd.notna(curr['VOL_MA_20']):
            if curr['volume'] > (curr['VOL_MA_20'] * 2.5):
                if curr['close'] > prev['close']:
                    signals.append("🔥 巨量拉升")
                else:
                    signals.append("😰 巨量砸盘")

        return price, signals
        
    except Exception as e:
        print(f"❌ 指标计算错误 {ticker}: {e}")
        return None, None

# ================= Bot 事件与指令 =================

@bot.event
async def on_ready():
    print(f'✅ 已登录: {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f'✅ 已同步 {len(synced)} 个斜杠命令')
    except Exception as e:
        print(f'❌ 同步命令失败: {e}')

    if not scanner_task.is_running():
        print("⏰ 启动定时监控任务...")
        scanner_task.start()

@bot.tree.command(name="test_signal", description="[测试] 立即分析一只股票的技术指标")
@app_commands.describe(ticker="股票代码 (例如 TSLA)")
async def test_signal(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    ticker = ticker.upper()
    price, signals = calculate_signals(ticker)
    
    if price is None:
        await interaction.followup.send(f"❌ 暂时无法获取 {ticker} 数据，请检查后台日志。")
        return

    if not signals:
        signals.append("平淡无奇，暂无明显信号")

    desc = "\n".join([f"• {s}" for s in signals])
    color = discord.Color.green() if "突破" in desc or "金叉" in desc else discord.Color.gold()
    
    embed = discord.Embed(
        title=f"🔍 FMP分析: {ticker}",
        description=f"**现价**: ${price:.2f}\n\n**当前信号**:\n{desc}",
        color=color
    )
    embed.set_image(url=get_finviz_chart_url(ticker))
    embed.set_footer(text="数据源: FMP Stable API • 15分钟周期")
    await interaction.followup.send(embed=embed)

@tasks.loop(minutes=15)
async def scanner_task():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    
    print(f"Running scan at {datetime.datetime.now()}")
    
    for ticker in WATCHLIST:
        try:
            price, signals = calculate_signals(ticker)
            
            if price is not None and signals:
                # 冷却检查
                now = datetime.datetime.now()
                if ticker in alert_cooldown:
                    if (now - alert_cooldown[ticker]).total_seconds() < 3 * 3600:
                        continue
                
                alert_cooldown[ticker] = now
                
                desc = "\n".join([f"• {s}" for s in signals])
                color = discord.Color.red() if "跌" in desc or "死叉" in desc or "砸盘" in desc else discord.Color.green()
                
                embed = discord.Embed(
                    title=f"⚡ 自动警报: {ticker}",
                    description=f"**现价**: ${price:.2f}\n\n{desc}",
                    color=color
                )
                embed.set_image(url=get_finviz_chart_url(ticker))
                embed.timestamp = now
                
                await channel.send(content=f"👀 {ticker} 出现信号", embed=embed)
                await asyncio.sleep(2)
                
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")

bot.run(TOKEN)
