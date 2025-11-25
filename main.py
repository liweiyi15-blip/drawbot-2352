import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests
import pandas as pd
import pandas_ta as ta
import numpy as np
import datetime
import os
import json
import asyncio
import pytz 
import math
import re
from dateutil import parser

# ================= 配置区域 =================
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') 

BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist_v29.json")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

watch_data = {}

# ================= 📖 战法说明书 (全覆盖版) =================
# 即使是空头信号，也用多头视角的“风险提示”口吻
SIGNAL_COMMENTS = {
    # --- 资金 ---
    "机构满仓": "资金疯狂涌入，主力隐秘抢筹。",
    "机构抛售": "主力大举出逃，切勿接飞刀。",
    "主力吸筹": "资金逆势流入，底部构筑中。",
    "主力派发": "股价虽涨资金流出，诱多风险。",
    "爆量抢筹": "阳线放巨量，真金白银进场。",
    "爆量出货": "阴线放巨量，抛压极其沉重。",
    "缩量上涨": "量价背离，上涨动能不足。",
    "缩量回调": "良性洗盘，惜售明显。",
    
    # --- 趋势 ---
    "Supertrend 看多": "站稳止损线，趋势向上。",
    "Supertrend 看空": "跌破止损线，趋势转空。",
    "云上金叉": "突破云层压力，真趋势确立。",
    "云下死叉": "被云层压制，空头趋势延续。",
    "站上云层": "多头突破阻力，趋势转强。",
    "跌破云层": "支撑失效，下方空间打开。",
    "Aroon 强多": "多头动能主导，持股待涨。",
    "Aroon 强空": "空头动能主导，切勿盲目抄底。",
    
    # --- 动能 ---
    "通道有效突破": "放量突破盘整，单边开启。",
    "通道有效跌破": "放量跌破盘整，加速下跌。",
    "ADX 多头加速": "趋势强度走高，顺势而为。",
    "ADX 空头加速": "恐慌盘涌出，下跌未止。",
    
    # --- 结构 ---
    "三线打击": "大阳吞没三阴，暴力反转。",
    "双底": "W底结构确认，颈线突破。",
    "双顶": "M头结构确认，见顶风险。",
    "回踩": "缩量回踩均线不破，买点。",
    
    # --- 摆动 ---
    "RSI 顶背离": "股价新高指标未新高，离场。",
    "RSI 底背离": "股价新低指标未新低，反弹酝酿。",
    "RSI 超买": "短线情绪过热，注意回调。",
    "RSI 超卖": "情绪冰点，博弈超跌反弹。",

    # --- 提示类 ---
    "锤子线": "低位长下影，资金尝试承接。",
    "早晨之星": "低位K线组合，黎明前的黑暗。",
    "恐慌极值": "带血筹码涌出，往往见底。",

    # --- 风控 ---
    "价值陷阱": "公司亏损 (EPS<0)，估值失效。",
    "黄金坑": "戴维斯双击：高盈利+低估值。",
    "财报": "财报窗口期波动剧烈，强行五五开，建议避险。",
    "九转": "情绪极值，变盘在即。",
    "板块": "所属板块整体走弱，覆巢之下无完卵。"
}

def get_comment(raw_text):
    # 优先匹配最长的key，防止部分匹配
    sorted_keys = sorted(SIGNAL_COMMENTS.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in raw_text: return SIGNAL_COMMENTS[key]
    return ""

# ================= 数据存取 =================
def load_data():
    global watch_data
    if os.path.exists(DATA_FILE):
        try: with open(DATA_FILE, 'r') as f: watch_data = json.load(f)
        except: watch_data = {}
    else: save_data()

def save_data():
    try: with open(DATA_FILE, 'w') as f: json.dump(watch_data, f, indent=4)
    except: pass

# ================= 🛡️ V29.3 评分系统 =================
def get_signal_score(s, regime="TREND"):
    s = s.strip()
    
    # --- A. 抄底雷达 (0分提示) ---
    if "💡" in s: return 0.0 

    # --- B. 资金博弈 ---
    if "CMF" in s:
        if "机构满仓" in s: return 2.5 
        if "机构抛售" in s: return -2.5
        if "主力吸筹" in s: return 1.5
        if "主力派发" in s: return -1.5
    
    if "量" in s:
        if "爆量抢筹" in s: return 2.0
        if "爆量出货" in s: return -2.0
        if "放量大涨" in s: return 1.0
        if "放量杀跌" in s: return -1.5
        if "缩量上涨" in s: return -1.0
        if "缩量回调" in s: return 0.5

    # --- C. 趋势核心 ---
    if "Supertrend 看多" in s: return 1.5
    if "Supertrend 看空" in s: return -1.5
    
    if "云上金叉" in s: return 2.5
    if "云下死叉" in s: return -2.5
    if "站上云层" in s: return 1.5
    if "跌破云层" in s: return -1.5
    
    if "Aroon 强多" in s: return 1.0
    if "Aroon 强空" in s: return -1.0

    # --- D. 动能 ---
    if "通道向上爆发" in s: return 1.5
    if "通道向下破位" in s: return -1.5
    
    if "ADX" in s:
        if "多头加速" in s: return 1.0
        if "空头加速" in s: return -1.0

    # --- E. 结构 ---
    if "三线打击" in s: return 2.5
    if "双底" in s: return 2.0
    if "双顶" in s: return -2.0
    if "三角旗" in s: return 1.5 if "突破" in s else -1.5
    if "回踩" in s: return 1.0

    # --- F. 摆动 ---
    if "底背离" in s: return 1.5
    if "顶背离" in s: return -1.5

    if "RSI" in s:
        if "超买" in s: return -1.0 if regime == "RANGE" else 0.0
        if "超卖" in s: return 1.0 if regime == "RANGE" else 0.0

    # --- G. 基本面/择时 ---
    if "价值陷阱" in s: return 0.0 
    if "黄金坑" in s: return 2.0
    if "九转" in s: return 2.0 if "底部" in s else -2.0
    if "华尔街" in s: return 1.0 if "买入" in s else -1.0
    if "财报" in s: return 0.0
    
    return 0

def generate_report_content(signals, regime="TREND"):
    items = []
    raw_score = 0.0
    has_bottom_signal = False
    
    # 触发“抄底雷达”的关键词
    bottom_keywords = ["黄金坑", "底背离", "九转: 底部", "锤子", "早晨之星", "双底", "恐慌极值"]

    for s in signals:
        score = get_signal_score(s, regime)
        if score != 0 or "财报" in s or "💡" in s:
            items.append({'raw': s, 'score': score})
            raw_score += score
        
        if any(k in s for k in bottom_keywords):
            has_bottom_signal = True

    items.sort(key=lambda x: abs(x['score']), reverse=True)
    
    final_blocks = []
    earnings_shown = False
    
    for item in items:
        if "财报" in item['raw']:
            if not earnings_shown:
                icon = "### 🚨 " if "高危" in item['raw'] else "### ⚠️ "
                comment = get_comment("财报")
                final_blocks.insert(0, f"{icon}{item['raw']}\n> {comment}")
                earnings_shown = True
            continue
        
        score_val = item['score']
        if score_val == 0:
            title = f"### {item['raw']}"
        else:
            score_str = f"+{score_val}" if score_val > 0 else f"{score_val}"
            title = f"### {item['raw']} ({score_str})"
        
        # 只要不是 0 分，或者 含有 💡，都显示
        if abs(score_val) >= 0.5 or "💡" in item['raw']:
            comment = get_comment(item['raw'])
            if comment:
                final_blocks.append(f"{title}\n> {comment}")
            else:
                final_blocks.append(title)

    final_text = "\n".join(final_blocks)
    main_reasons = [x['raw'] for x in items if abs(x['score']) >= 1.5 or "财报" in x['raw'] or "💡" in x['raw']][:3]
    
    return raw_score, final_text, main_reasons, has_bottom_signal

def format_dashboard_title(score, has_bottom_signal=False):
    count = min(int(round(abs(score))), 10)
    icons = "⭐" * count if score > 0 else "💀" * count if score < 0 else "⚖️"
    status, color = "震荡", discord.Color.light_grey()
    
    if score >= 1.0: 
        if score >= 8.0: status, color = "机构重仓", discord.Color.from_rgb(0, 255, 0)
        elif score >= 5.0: status, color = "多头共振", discord.Color.green()
        else: status, color = "趋势向上", discord.Color.blue()
    elif score <= -1.0:
        if score <= -8.0: status, color = "清仓离场", discord.Color.from_rgb(255, 0, 0)
        elif score <= -5.0: status, color = "空头共振", discord.Color.red()
        else: status, color = "趋势向下", discord.Color.orange()
    else:
        status, color = "多空平衡", discord.Color.gold()
        icons = "⚖️"
    
    if has_bottom_signal and score < 5.0:
        status += " 🎣" 
        
    return f"{status} ({score:+.1f}) {icons}", color

# ================= 📈 V29.3 核心分析逻辑 =================
def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 100: return None, None, None, None
    
    df.columns = [str(c).upper() for c in df.columns]
    signals = []
    
    # 1. 指标计算
    df.ta.supertrend(length=10, multiplier=3, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.aroon(length=25, append=True)
    df.ta.cmf(length=20, append=True)
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.ta.kc(length=20, scalar=2, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    try: df.ta.cdl_pattern(name=["hammer", "morning_star"], append=True)
    except: pass
    
    # 一目均衡
    high9 = df['HIGH'].rolling(9).max(); low9 = df['LOW'].rolling(9).min()
    df['tenkan'] = (high9 + low9) / 2
    high26 = df['HIGH'].rolling(26).max(); low26 = df['LOW'].rolling(26).min()
    df['kijun'] = (high26 + low26) / 2
    high52 = df['HIGH'].rolling(52).max(); low52 = df['LOW'].rolling(52).min()
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    df['senkou_b'] = ((high52 + low52) / 2).shift(26)

    curr = df.iloc[-1]; prev = df.iloc[-2]; price = curr['CLOSE']
    market_regime = "TREND" if (curr.get('ADX_14', 0) > 25) else "RANGE"

    # 0. 估值 & 财报
    signals.extend(get_valuation_and_earnings(ticker, price))

    # 1. 趋势
    st_cols = [c for c in df.columns if c.startswith('SUPERT')]
    st_col = st_cols[0] if st_cols else None
    is_bull = False
    if st_col:
        if curr['CLOSE'] > curr[st_col]: 
            signals.append("Supertrend 看多")
            is_bull = True
        else: signals.append("Supertrend 看空")

    kumo_top = max(curr['senkou_a'], curr['senkou_b'])
    kumo_bottom = min(curr['senkou_a'], curr['senkou_b'])
    if price > kumo_top: 
        signals.append("一目均衡: 站上云层")
        if curr['tenkan'] > curr['kijun'] and prev['tenkan'] <= prev['kijun']:
            signals.append("一目均衡: 云上金叉")
    elif price < kumo_bottom:
        signals.append("一目均衡: 跌破云层")
        if curr['tenkan'] < curr['kijun'] and prev['tenkan'] >= prev['kijun']:
            signals.append("一目均衡: 云下死叉")

    if 'AROONU_25' in df.columns:
        if curr['AROONU_25'] > 70 and curr['AROOND_25'] < 30: signals.append("Aroon 强多")
        elif curr['AROOND_25'] > 70 and curr['AROONU_25'] < 30: signals.append("Aroon 强空")

    # 2. 资金
    if 'CMF_20' in df.columns:
        cmf = curr['CMF_20']
        if cmf > 0.25: signals.append(f"CMF 机构满仓 (极强) [{cmf:.2f}]")
        elif cmf < -0.25: signals.append(f"CMF 机构抛售 (极强) [{cmf:.2f}]")
        elif cmf > 0.20: signals.append(f"CMF 主力吸筹 (强) [{cmf:.2f}]")
        elif cmf < -0.20: signals.append(f"CMF 主力派发 (强) [{cmf:.2f}]")

    vol_ma = curr['VOL_MA_20']
    rvol = 0
    if pd.notna(vol_ma) and vol_ma > 0:
        rvol = curr['VOLUME'] / vol_ma
        is_green = curr['CLOSE'] > curr['OPEN']
        if rvol > 2.0:
            if is_green: signals.append(f"量: 爆量抢筹 [量比:{rvol:.1f}x]")
            else: signals.append(f"量: 爆量出货 [量比:{rvol:.1f}x]")
        elif rvol > 1.5:
            if curr['CLOSE'] > prev['CLOSE']: signals.append(f"量: 放量大涨 [量比:{rvol:.1f}x]")
            else: signals.append(f"量: 放量杀跌 [量比:{rvol:.1f}x]")
        elif rvol < 0.8:
            if curr['CLOSE'] > prev['CLOSE']: signals.append("量: 缩量上涨 (量价背离)")
            else: signals.append("量: 缩量回调")

    # 3. 动能
    kc_up = [c for c in df.columns if c.startswith('KCU')][0] if [c for c in df.columns if c.startswith('KCU')] else None
    kc_low = [c for c in df.columns if c.startswith('KCL')][0] if [c for c in df.columns if c.startswith('KCL')] else None
    adx_val = curr.get('ADX_14', 0)
    
    if kc_up and price > curr[kc_up]:
        if adx_val > 20 and rvol > 1.0: signals.append("肯特纳: 通道有效突破")
    elif kc_low and price < curr[kc_low]:
        if adx_val > 20 and rvol > 1.0: signals.append("肯特纳: 通道有效跌破")

    if adx_val > 25:
        trend = "多头" if is_bull else "空头"
        signals.append(f"ADX {trend}加速 [{adx_val:.1f}]")

    # 4. 抄底雷达
    if curr['RSI_14'] < 20 and rvol > 1.5: signals.append("💡 恐慌极值 (带血筹码)")
    cols = df.columns
    if curr['RSI_14'] < 40: 
        if any('HAMMER' in c for c in cols) and df.filter(like='HAMMER').iloc[-1].item() != 0:
            signals.append("💡 K线: 锤子线 (低位探底)")
        if any('MORNING' in c for c in cols) and df.filter(like='MORNING').iloc[-1].item() != 0:
            signals.append("💡 K线: 早晨之星 (低位反转)")

    # 5. 结构
    try:
        ma200 = df['CLOSE'].rolling(200).mean().iloc[-1]
        if price < ma200 * 1.1: 
            lows = df['LOW'].iloc[-60:]
            min1 = lows.iloc[:30].min(); min2 = lows.iloc[30:].min()
            if abs(min1 - min2) < min1 * 0.03 and price > min1 * 1.05:
                signals.append("🇼 双底结构")
    except: pass
    
    if (df['CLOSE'].iloc[-2] < df['OPEN'].iloc[-2]) and \
       (df['CLOSE'].iloc[-3] < df['OPEN'].iloc[-3]) and \
       (df['CLOSE'].iloc[-4] < df['OPEN'].iloc[-4]) and \
       (curr['CLOSE'] > curr['OPEN']) and \
       (curr['CLOSE'] > df['OPEN'].iloc[-4]):
        signals.append("💂‍♂️ 三线打击")

    ma20 = df['CLOSE'].rolling(20).mean().iloc[-1]
    if is_bull and curr['LOW'] <= ma20 * 1.015 and curr['CLOSE'] > ma20:
        signals.append("回踩 MA20 获支撑")

    # 6. 摆动
    rsi_val = curr['RSI_14']
    if rsi_val > 75: signals.append(f"RSI 超买 [{rsi_val:.1f}]")
    elif rsi_val < 30: signals.append(f"RSI 超卖 [{rsi_val:.1f}]")

    try:
        lookback = 30
        recent_df = df.iloc[-lookback:]
        p_high_idx = recent_df['HIGH'].idxmax()
        if (df.index[-1] - p_high_idx).days <= 10:
            r_at_high = recent_df.loc[p_high_idx, 'RSI_14']
            prev_rsi_max = df['RSI_14'].iloc[-60:-lookback].max()
            if r_at_high < prev_rsi_max and rsi_val < 70: signals.append("RSI 顶背离 (离场)")

        p_low_idx = recent_df['LOW'].idxmin()
        if (df.index[-1] - p_low_idx).days <= 10:
            r_at_low = recent_df.loc[p_low_idx, 'RSI_14']
            prev_rsi_min = df['RSI_14'].iloc[-60:-lookback].min()
            if r_at_low > prev_rsi_min and rsi_val > 30: signals.append("RSI 底背离 (抄底)")
    except: pass

    # 7. 九转
    try:
        c = df['CLOSE'].values
        buy_s = 0; sell_s = 0
        for i in range(4, len(c)):
            if c[i] > c[i-4]: sell_s += 1; buy_s = 0
            elif c[i] < c[i-4]: buy_s += 1; sell_s = 0
            else: buy_s = 0; sell_s = 0
        if buy_s == 9: signals.append("九转: 底部买入信号 [9]")
        elif sell_s == 9: signals.append("九转: 顶部卖出信号 [9]")
    except: pass

    # 🛡️ 止损位计算 (永远多头视角)
    atr = curr.get('ATRr_14', 0) if 'ATRr_14' in curr else curr.get('ATR_14', 0)
    stop_loss_price = 0
    
    if atr > 0:
        # 如果是多头趋势，用 ATR Trailing Stop (紧跟)
        if is_bull and st_col:
            st_val = curr[st_col]
            stop_loss_price = max(st_val * 0.985, price - 2.8 * atr)
        else:
            # 🔥 如果是空头趋势，止损位其实是“压力位/翻多点”
            # 逻辑：突破 Supertrend 或 现价 + 2.8 ATR 视为反转
            if st_col:
                stop_loss_price = curr[st_col] # 空头趋势下，Supertrend 在上方，是天然压力位
            else:
                stop_loss_price = price + 2.8 * atr
    
    return price, signals, market_regime, stop_loss_price

# ================= Bot 指令 =================
@bot.tree.command(name="check", description="机构精核分析 (单只)")
async def check_stocks(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    t = ticker.split()[0].replace(',', '').upper()
    loop = asyncio.get_running_loop()
    price, signals, regime, stop_loss = await loop.run_in_executor(None, analyze_daily_signals, t)
    
    if price is None:
        return await interaction.followup.send(f"❌ 数据获取失败: {t}")
    if not signals: signals.append("多空平衡")
    
    score, desc, _, has_bottom = generate_report_content(signals, regime)
    title, color = format_dashboard_title(score, has_bottom)
    
    # 🏷️ 纯多头视角文案修正
    if stop_loss > price:
        stop_label = "上方压力 (翻多点)"
    else:
        stop_label = "多头止损"
        
    # 🎣 抄底文字提示 (如果触发雷达)
    bottom_text = "\n💡 **触发抄底信号**" if has_bottom else ""
    
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    
    embed = discord.Embed(title=f"{t} : {title}", description=f"**现价**: ${price:.2f} | {stop_label}: ${stop_loss:.2f}{bottom_text}\n\n{desc}", color=color)
    embed.set_image(url=get_finviz_chart_url(t))
    embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="list", description="查看核心看板")
async def list_stocks(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_stocks = watch_data.get(user_id, {})
    if not user_stocks: return await interaction.response.send_message("📭 列表为空")
    
    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()
    tasks_list = []
    tickers = list(user_stocks.keys())
    for t in tickers:
        tasks_list.append(loop.run_in_executor(None, analyze_daily_signals, t))
    
    results = await asyncio.gather(*tasks_list)
    lines = []
    for i, (p, s, r, stop) in enumerate(results):
        t = tickers[i]
        if p is None: continue
        score, _, reasons, has_bottom = generate_report_content(s, r)
        
        raw_reason = reasons[0] if reasons else "多空平衡"
        clean_reason = re.sub(r"[\(\[].*?[\)\]]", "", raw_reason)
        clean_reason = re.sub(r"[\+\-\$\d\.\:]", "", clean_reason).strip()
        
        title, _ = format_dashboard_title(score, has_bottom)
        short_status = title.split(' ')[0]
        icons = title.split(' ')[2]
        if "🎣" in title and "🎣" not in icons: icons += " 🎣"
            
        lines.append(f"**{t}**: {short_status} {icons}\n└ {clean_reason}")
    
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    embed = discord.Embed(title="📊 监控面板", description="\n".join(lines), color=discord.Color.blue())
    embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="scores", description="查看V29评分与风控表")
async def show_scores(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 V29.3 机构多头猎手评分表", description="权重根据实盘胜率进行非线性调整。全程**多头视角**，下跌趋势中提示压力位与抄底信号。", color=discord.Color.gold())
    
    embed.add_field(name="🚀 核弹信号 (Win Rate > 80%)", value="""
`±3.3` 云上金叉 / 云下死叉
`±3.1` CMF机构满仓 / CMF机构抛售
`+3.0` 三线打击
""", inline=False)

    embed.add_field(name="💎 核心驱动 (Win Rate > 70%)", value="""
`+2.8` 双底结构
`+2.7` 爆量抢筹
`-2.9` 爆量出货
`+2.2` 黄金坑
`±2.0` 九转极值
""", inline=False)

    embed.add_field(name="⚖️ 趋势与辅助", value="""
`±1.6` Supertrend
`±1.5` 站上/跌破云层 | 通道突破 | CMF吸筹
`±1.0` Aroon | ADX加速 | 回踩 | 华尔街
""", inline=False)
    
    embed.add_field(name="🎣 抄底雷达 (0分提示)", value="""
`💡 ` **K线**: 锤子线/早晨之星 (限RSI<40)
`💡 ` **恐慌**: 恐慌极值 (RSI<20 + 爆量)
`注` 触发雷达时，面板会显示 🎣 图标和文字提示
""", inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="add", description="批量添加")
@app_commands.describe(ticker="代码", mode="模式")
async def add_stock(interaction: discord.Interaction, ticker: str, mode: str = "once_daily"):
    user_id = str(interaction.user.id)
    if user_id not in watch_data: watch_data[user_id] = {}
    for t in ticker.upper().replace(',', ' ').split():
        watch_data[user_id][t] = {"mode": mode, "last_alert_date": ""}
    save_data()
    await interaction.response.send_message(f"✅ 已添加: {ticker}")

@bot.tree.command(name="remove", description="删除")
async def remove_stock(interaction: discord.Interaction, ticker: str):
    user_id = str(interaction.user.id)
    t = ticker.upper()
    if user_id in watch_data and t in watch_data[user_id]:
        del watch_data[user_id][t]
        save_data()
        await interaction.response.send_message(f"🗑️ 已删除 {t}")
    else: await interaction.response.send_message("❓ 未找到")

@tasks.loop(time=datetime.time(hour=16, minute=1, tzinfo=pytz.timezone('America/New_York')))
async def daily_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    loop = asyncio.get_running_loop()
    
    for uid, stocks in watch_data.items():
        alerts = []
        tickers = list(stocks.keys())
        tasks = [loop.run_in_executor(None, analyze_daily_signals, t) for t in tickers]
        results = await asyncio.gather(*tasks)
        
        for i, (p, s, r, stop) in enumerate(results):
            if not s: continue
            score, desc, _, has_bottom = generate_report_content(s, r)
            t = tickers[i]
            if stocks[t]['mode'] == 'always' or stocks[t]['last_alert_date'] != today:
                stocks[t]['last_alert_date'] = today
                title, color = format_dashboard_title(score, has_bottom)
                
                # 🏷️ 动态修改止损标签
                stop_label = "上方压力 (翻多点)" if stop > p else "多头止损"
                bottom_text = "\n💡 **触发抄底信号**" if has_bottom else ""
                
                emb = discord.Embed(title=f"{t}: {title}", description=f"${p:.2f} | {stop_label}: ${stop:.2f}{bottom_text}\n{desc}", color=color)
                emb.set_image(url=get_finviz_chart_url(t))
                emb.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
                alerts.append(emb)
        
        if alerts:
            save_data()
            await channel.send(f"🔔 <@{uid}> 收盘日报:")
            for a in alerts: await channel.send(embed=a)

@bot.event
async def on_ready():
    load_data()
    print("✅ V29.3 机构多头猎手版启动")
    await bot.tree.sync()
    daily_monitor.start()

bot.run(TOKEN)
