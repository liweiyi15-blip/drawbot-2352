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

# 持久化路径
BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist_v28.json")

# Bot 设置
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

watch_data = {}

# ================= 📖 战法说明书 (20字内人话解释) =================
SIGNAL_COMMENTS = {
    # --- 趋势 (Trend) ---
    "Supertrend 看多": "站稳止损线，趋势向上。",
    "Supertrend 看空": "跌破止损线，趋势转空。",
    "云上金叉": "突破云层压力，真趋势确立。",
    "云下死叉": "被云层压制，空头趋势延续。",
    "站上云层": "多头突破阻力，趋势转强。",
    "跌破云层": "支撑失效，下方空间打开。",
    "Aroon 强多": "多头动能主导，趋势极强。",
    "Aroon 强空": "空头动能主导，切勿抄底。",
    
    # --- 资金 (Volume) ---
    "主力吸筹": "资金逆势流入，底部构筑。",
    "主力派发": "股价涨资金流出，诱多风险。",
    "爆量抢筹": "阳线放巨量，真金白银进场。",
    "爆量出货": "阴线放巨量，主力大举出逃。",
    "缩量上涨": "量价背离，上涨动能衰竭。",
    "缩量回调": "良性洗盘，惜售明显。",
    "放量大涨": "量价齐升，上涨健康。",
    "放量杀跌": "恐慌盘涌出，承接无力。",
    
    # --- 动能 (Momentum) ---
    "通道向上爆发": "突破盘整区间，单边行情开启。",
    "通道向下破位": "跌破盘整区间，加速下跌。",
    "ADX 多头加速": "趋势强度走高，顺势而为。",
    "ADX 空头加速": "恐慌盘涌出，加速下跌。",
    
    # --- 结构 (Pattern) ---
    "三线打击": "大阳吞没三阴，暴力反转。",
    "双底": "W底结构确认，颈线突破。",
    "双顶": "M头结构确认，见顶风险。",
    "三角旗": "中继形态整理结束，选择方向。",
    "回踩": "缩量回踩均线不破，买点。",
    
    # --- 摆动 (Oscillator) ---
    "RSI 顶背离": "股价新高指标未新高，离场。",
    "RSI 底背离": "股价新低指标未新低，抄底。",
    "RSI 超买": "短线情绪过热，注意回调。",
    "RSI 超卖": "情绪冰点，博弈超跌反弹。",

    # --- 熔断/风控 ---
    "价值陷阱": "公司亏损 (EPS<0)，估值失效。",
    "黄金坑": "盈利好且估值低，戴维斯双击。",
    "财报": "窗口期波动剧烈，建议避险。",
    "九转": "情绪极值，变盘在即。"
}

def get_comment(raw_text):
    for key, comment in SIGNAL_COMMENTS.items():
        if key in raw_text: return comment
    return ""

# ================= 数据存取 (已修复语法错误) =================
def load_data():
    global watch_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                watch_data = json.load(f)
        except:
            watch_data = {}
    else:
        save_data()

def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(watch_data, f, indent=4)
    except:
        pass

# ================= 🛡️ V28.1 机构精核评分 (Regime Scoring) =================
def get_signal_score(s, regime="TREND"):
    s = s.strip()
    
    # --- A. 趋势核心 (Trend) ---
    if "Supertrend 看多" in s: return 1.5
    if "Supertrend 看空" in s: return -1.5
    
    # 一目均衡 (权重之王)
    if "云上金叉" in s: return 2.5
    if "云下死叉" in s: return -2.5
    if "站上云层" in s: return 1.5
    if "跌破云层" in s: return -1.5
    
    # Aroon (趋势验证)
    if "Aroon 强多" in s: return 1.0
    if "Aroon 强空" in s: return -1.0

    # --- B. 资金博弈 (Money) ---
    if "CMF" in s:
        if "主力吸筹" in s: return 1.5
        if "主力派发" in s: return -1.5
    
    if "量" in s:
        if "爆量抢筹" in s: return 2.0
        if "爆量出货" in s: return -2.0
        if "放量大涨" in s: return 1.0
        if "放量杀跌" in s: return -1.5
        if "缩量上涨" in s: return -1.0 # 隐患
        if "缩量回调" in s: return 0.5

    # --- C. 动能 (Momentum) ---
    if "通道向上爆发" in s: return 1.5
    if "通道向下破位" in s: return -1.5
    
    if "ADX" in s:
        if "多头加速" in s: return 1.0
        if "空头加速" in s: return -1.0

    # --- D. 结构 (Pattern) ---
    if "三线打击" in s: return 2.5
    if "双底" in s: return 2.0
    if "双顶" in s: return -2.0
    if "三角旗" in s: return 1.5 if "突破" in s else -1.5
    if "回踩" in s: return 1.0

    # --- E. 摆动 (Oscillator) - 体制过滤核心逻辑 ---
    
    # 1. 背离 (无论什么体制都是强信号)
    if "底背离" in s: return 1.5
    if "顶背离" in s: return -1.5

    # 2. 超买超卖 (受体制过滤)
    if "RSI" in s:
        if "超买" in s: 
            # 趋势市(TREND): 忽略 (0分)
            # 震荡市(RANGE): 卖点 (-1.0)
            return -1.0 if regime == "RANGE" else 0.0
        if "超卖" in s: 
            # 趋势市(TREND): 忽略 (0分, 不接飞刀)
            # 震荡市(RANGE): 买点 (+1.0)
            return 1.0 if regime == "RANGE" else 0.0

    # --- F. 基本面/择时 ---
    if "价值陷阱" in s: return 0.0 # 熔断
    if "黄金坑" in s: return 1.5
    if "九转" in s: return 2.0 if "底部" in s else -2.0
    if "华尔街" in s: return 1.0 if "买入" in s else -1.0
    
    return 0

def generate_report_content(signals, regime="TREND"):
    items = []
    raw_score = 0.0
    for s in signals:
        score = get_signal_score(s, regime)
        if score != 0:
            items.append({'raw': s, 'score': score})
            raw_score += score

    items.sort(key=lambda x: abs(x['score']), reverse=True)
    
    final_blocks = []
    earnings_shown = False
    
    for item in items:
        # 财报强制置顶
        if "财报" in item['raw']:
            if not earnings_shown:
                icon = "### 🚨 " if "高危" in item['raw'] else "### ⚠️ "
                comment = get_comment("财报")
                final_blocks.insert(0, f"{icon}{item['raw']}\n> {comment}")
                earnings_shown = True
            continue
        
        score_val = item['score']
        score_str = f"+{score_val}" if score_val > 0 else f"{score_val}"
        
        # 只有绝对分值 >= 0.5 才显示，过滤噪音
        if abs(score_val) >= 0.5:
            title = f"### {item['raw']} ({score_str})"
            # 自动匹配评论
            key_for_comment = ""
            for k in SIGNAL_COMMENTS.keys():
                if k in item['raw']: 
                    key_for_comment = k
                    break
            
            if key_for_comment:
                comment = SIGNAL_COMMENTS[key_for_comment]
                final_blocks.append(f"{title}\n> {comment}")
            else:
                final_blocks.append(title)

    final_text = "\n".join(final_blocks)
    main_reasons = [x['raw'] for x in items if abs(x['score']) >= 1.5][:3]
    return raw_score, final_text, main_reasons

def format_dashboard_title(score):
    count = min(int(round(abs(score))), 10)
    icons = "⭐" * count if score > 0 else "💀" * count if score < 0 else "⚖️"
    status, color = "震荡", discord.Color.light_grey()
    
    if score >= 8.0: status, color = "机构重仓", discord.Color.from_rgb(0, 255, 0)
    elif score >= 5.0: status, color = "多头共振", discord.Color.green()
    elif score >= 2.0: status, color = "趋势向上", discord.Color.blue()
    elif score <= -8.0: status, color = "清仓离场", discord.Color.from_rgb(255, 0, 0)
    elif score <= -5.0: status, color = "空头共振", discord.Color.red()
    elif score <= -2.0: status, color = "趋势向下", discord.Color.orange()
    else: status, color = "多空平衡", discord.Color.gold()
    
    return f"{status} ({score:+.1f}) {icons}", color

# ================= 📈 V28.1 核心分析逻辑 (含背离) =================
def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 100: return None, None
    signals = []
    
    # 1. 指标计算 (只算高信噪比的)
    df.ta.supertrend(length=10, multiplier=3, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.aroon(length=25, append=True)
    df.ta.cmf(length=20, append=True)
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.ta.kc(length=20, scalar=2, append=True) # 肯特纳通道
    df.ta.rsi(length=14, append=True)
    
    # 手动计算一目均衡 (最核心风控)
    high9 = df['high'].rolling(9).max(); low9 = df['low'].rolling(9).min()
    df['tenkan'] = (high9 + low9) / 2
    high26 = df['high'].rolling(26).max(); low26 = df['low'].rolling(26).min()
    df['kijun'] = (high26 + low26) / 2
    high52 = df['high'].rolling(52).max(); low52 = df['low'].rolling(52).min()
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    df['senkou_b'] = ((high52 + low52) / 2).shift(26)

    curr = df.iloc[-1]; prev = df.iloc[-2]; price = curr['CLOSE']

    # === 判断市场体制 (Regime) ===
    # 如果 ADX > 25，定义为趋势市 (TREND)，屏蔽 RSI 超买信号
    market_regime = "TREND" if (curr.get('ADX_14', 0) > 25) else "RANGE"

    # 0. 估值 & 财报
    signals.extend(get_valuation_and_earnings(ticker, price))

    # 1. 趋势 (Trend) - 以云层为基准
    st_cols = [c for c in df.columns if c.startswith('SUPERT')]
    st_col = st_cols[0] if st_cols else None
    
    if st_col:
        if curr['CLOSE'] > curr[st_col]: signals.append("Supertrend 看多")
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

    # 2. 资金 (Volume) - 严格的真假阳线判断
    if 'CMF_20' in df.columns:
        cmf = curr['CMF_20']
        if cmf > 0.20: signals.append(f"CMF 主力吸筹 (强) [{cmf:.2f}]")
        elif cmf < -0.20: signals.append(f"CMF 主力派发 (强) [{cmf:.2f}]")

    vol_ma = curr['VOL_MA_20']
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

    # 3. 动能 (Momentum)
    kc_up = [c for c in df.columns if c.startswith('KCU')][0] if [c for c in df.columns if c.startswith('KCU')] else None
    kc_low = [c for c in df.columns if c.startswith('KCL')][0] if [c for c in df.columns if c.startswith('KCL')] else None
    
    if kc_up and price > curr[kc_up]: signals.append("肯特纳: 通道向上爆发")
    elif kc_low and price < curr[kc_low]: signals.append("肯特纳: 通道向下破位")

    if curr.get('ADX_14', 0) > 25:
        trend = "多头" if (st_col and curr['CLOSE'] > curr[st_col]) else "空头"
        signals.append(f"ADX {trend}加速 [{curr['ADX_14']:.1f}]")

    # 4. 结构 (Pattern)
    # 双底逻辑
    try:
        ma200 = df['CLOSE'].rolling(200).mean().iloc[-1]
        if price < ma200 * 1.1: 
            lows = df['LOW'].iloc[-60:]
            min1 = lows.iloc[:30].min(); min2 = lows.iloc[30:].min()
            if abs(min1 - min2) < min1 * 0.03 and price > min1 * 1.05:
                signals.append("🇼 双底结构")
    except: pass
    
    # 三线打击
    if (df['CLOSE'].iloc[-2] < df['OPEN'].iloc[-2]) and \
       (df['CLOSE'].iloc[-3] < df['OPEN'].iloc[-3]) and \
       (df['CLOSE'].iloc[-4] < df['OPEN'].iloc[-4]) and \
       (curr['CLOSE'] > curr['OPEN']) and \
       (curr['CLOSE'] > df['OPEN'].iloc[-4]):
        signals.append("💂‍♂️ 三线打击")

    # 回踩
    ma20 = df['CLOSE'].rolling(20).mean().iloc[-1]
    if st_col and (curr['CLOSE'] > curr[st_col]) and curr['LOW'] <= ma20 * 1.015 and curr['CLOSE'] > ma20:
        signals.append("回踩 MA20 获支撑")

    # 5. 摆动 (RSI: 背离 + 体制过滤)
    rsi_val = curr['RSI_14']
    
    # A. 基础超买超卖
    if rsi_val > 75: signals.append(f"RSI 超买 [{rsi_val:.1f}]")
    elif rsi_val < 30: signals.append(f"RSI 超卖 [{rsi_val:.1f}]")

    # B. 背离检测
    try:
        lookback = 30
        recent_df = df.iloc[-lookback:]
        
        # 顶背离
        p_high_idx = recent_df['HIGH'].idxmax()
        if (df.index[-1] - p_high_idx).days <= 10:
            r_at_high = recent_df.loc[p_high_idx, 'RSI_14']
            prev_rsi_max = df['RSI_14'].iloc[-60:-lookback].max()
            if r_at_high < prev_rsi_max and rsi_val < 70:
                 signals.append("RSI 顶背离 (离场)")

        # 底背离
        p_low_idx = recent_df['LOW'].idxmin()
        if (df.index[-1] - p_low_idx).days <= 10:
            r_at_low = recent_df.loc[p_low_idx, 'RSI_14']
            prev_rsi_min = df['RSI_14'].iloc[-60:-lookback].min()
            if r_at_low > prev_rsi_min and rsi_val > 30:
                signals.append("RSI 底背离 (抄底)")
    except: pass

    # 6. 九转
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

    return price, signals, market_regime

# ================= 辅助函数 (FMP & Data) =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_valuation_and_earnings(ticker, current_price):
    if not FMP_API_KEY: return []
    sigs = []
    try:
        # 1. 财报
        today = datetime.date.today()
        future_str = (today + datetime.timedelta(days=14)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')
        cal_url = f"https://financialmodelingprep.com/stable/earnings-calendar?from={today_str}&to={future_str}&apikey={FMP_API_KEY}"
        cal_resp = requests.get(cal_url, timeout=5)
        if cal_resp.status_code == 200:
            for entry in cal_resp.json():
                if ticker == entry.get('symbol'):
                    d_str = entry.get('date')
                    if d_str:
                        diff = (parser.parse(d_str).date() - today).days
                        if 0 <= diff <= 14: sigs.append(f"财报预警 [T-{diff}天]")
                        break 
        
        # 2. 估值 (EPS熔断逻辑)
        r_url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={FMP_API_KEY}"
        r_resp = requests.get(r_url, timeout=5)
        if r_resp.status_code == 200:
            r_data = r_resp.json()
            if r_data:
                rd = r_data[0]
                eps = rd.get('netIncomePerShareTTM', 0)
                pe = rd.get('priceToEarningsRatioTTM')
                
                if eps is None or eps <= 0:
                    sigs.append("价值陷阱 (EPS<0)")
                else:
                    h_url = f"https://financialmodelingprep.com/stable/ratios?symbol={ticker}&limit=3&apikey={FMP_API_KEY}"
                    h_resp = requests.get(h_url, timeout=5)
                    if h_resp.status_code == 200:
                        h_data = h_resp.json()
                        pe_list = [x.get('priceToEarningsRatio', 0) for x in h_data if x.get('priceToEarningsRatio', 0)>0]
                        if pe_list:
                            avg_pe = sum(pe_list)/len(pe_list)
                            if pe and pe < avg_pe * 0.8: sigs.append(f"黄金坑 (历史低位) [PE:{pe:.1f}]")
        
        # 3. 华尔街
        rec_url = f"https://financialmodelingprep.com/stable/analyst-stock-recommendations?symbol={ticker}&apikey={FMP_API_KEY}"
        rec_resp = requests.get(rec_url, timeout=5)
        if rec_resp.status_code == 200:
            rec_data = rec_resp.json()
            if rec_data:
                rd = rec_data[0]
                buy = rd.get('analystRatingsbuy', 0) + rd.get('analystRatingsStrongBuy', 0)
                sell = rd.get('analystRatingsSell', 0) + rd.get('analystRatingsStrongSell', 0)
                total = buy + sell + rd.get('analystRatingsHold', 0)
                if total > 0:
                    if buy/total > 0.7: sigs.append("🏦 华尔街共识: 买入")
                    elif sell/total > 0.5: sigs.append("🏦 华尔街共识: 卖出")
    except: pass
    return sigs

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
            new_row = {'date': today_str, 'open': curr.get('open', df['close'].iloc[-1]), 'high': curr.get('dayHigh', curr['price']), 'low': curr.get('dayLow', curr['price']), 'close': curr['price'], 'volume': curr.get('volume', 0)}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df
    except: return None

# ================= Bot 指令 =================
@bot.tree.command(name="check", description="机构精核分析 (单只)")
async def check_stocks(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    t = ticker.split()[0].replace(',', '').upper()
    loop = asyncio.get_running_loop()
    price, signals, regime = await loop.run_in_executor(None, analyze_daily_signals, t)
    
    if price is None:
        return await interaction.followup.send(f"❌ 数据获取失败: {t}")
    if not signals: signals.append("多空平衡")
    
    score, desc, _ = generate_report_content(signals, regime)
    title, color = format_dashboard_title(score)
    
    regime_text = "🌊 强趋势市 (Trend Mode)" if regime == "TREND" else "🦀 震荡整理 (Range Mode)"
    
    embed = discord.Embed(title=f"{t} : {title}", description=f"**现价**: ${price:.2f}\n\n{desc}", color=color)
    embed.set_image(url=get_finviz_chart_url(t))
    embed.set_footer(text=f"FMP V28.1 机构精核版 • {regime_text}")
    
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
    for i, (price, signals, regime) in enumerate(results):
        t = tickers[i]
        if price is None: continue
        score, _, reasons = generate_report_content(signals, regime)
        # 清洗文本
        raw_reason = reasons[0] if reasons else "多空平衡"
        clean_reason = re.sub(r"[\(\[].*?[\)\]]", "", raw_reason)
        clean_reason = re.sub(r"[\+\-\$\d\.\:]", "", clean_reason).strip()
        
        title, _ = format_dashboard_title(score)
        short_status = title.split(' ')[0]
        icons = title.split(' ')[2]
        lines.append(f"**{t}**: {short_status} {icons}\n└ {clean_reason}")
    
    embed = discord.Embed(title="📊 监控面板", description="\n".join(lines), color=discord.Color.blue())
    embed.set_footer(text=f"FMP V28.1 • {datetime.datetime.now().strftime('%H:%M')}")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="scores", description="查看V28精核评分表")
async def show_scores(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 V28.1 机构精核评分表", description="已剔除MACD等滞后指标，仅保留高信噪比信号。引入 **体制过滤**: 趋势市中自动屏蔽RSI超买信号。", color=discord.Color.gold())
    
    embed.add_field(name="🚀 核心驱动 (Trend & Money)", value="""
`+2.5` 云上金叉 / 三线打击
`-2.5` 云下死叉
`+2.0` 爆量抢筹 (阳) / 九转底部
`-2.0` 爆量出货 (阴) / 九转顶部
`+1.5` Supertrend多 / 主力吸筹 / 黄金坑
`-1.5` Supertrend空 / 主力派发
""", inline=False)

    embed.add_field(name="⚖️ 辅助验证 (Momentum)", value="""
`+1.5` 通道爆发 / 站上云层 / RSI底背离
`-1.5` 通道破位 / 跌破云层 / RSI顶背离
`+1.0` ADX多头加速 / 放量大涨 / 回踩
`-1.5` ADX空头加速 / 放量杀跌
`-1.0` 缩量上涨 (背离)
""", inline=False)
    
    embed.add_field(name="📉 熔断与体制", value="""
` 0.0` **价值陷阱** (EPS<0 时低估值无效)
`⚠️ ` **财报预警** (强制置顶)
`🛡️ ` **趋势体制**: ADX>25 时，RSI超买信号失效
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
    loop = asyncio.get_running_loop()
    
    for uid, stocks in watch_data.items():
        alerts = []
        tickers = list(stocks.keys())
        tasks = [loop.run_in_executor(None, analyze_daily_signals, t) for t in tickers]
        results = await asyncio.gather(*tasks)
        
        for i, (p, s, r) in enumerate(results):
            if not s: continue
            score, desc, _ = generate_report_content(s, r)
            t = tickers[i]
            if stocks[t]['mode'] == 'always' or stocks[t]['last_alert_date'] != today:
                stocks[t]['last_alert_date'] = today
                title, color = format_dashboard_title(score)
                emb = discord.Embed(title=f"{t}: {title}", description=f"${p:.2f}\n{desc}", color=color)
                emb.set_image(url=get_finviz_chart_url(t))
                alerts.append(emb)
        
        if alerts:
            save_data()
            await channel.send(f"🔔 <@{uid}> 收盘日报:")
            for a in alerts: await channel.send(embed=a)

@bot.event
async def on_ready():
    load_data()
    print("✅ V28.1 机构精核版启动")
    await bot.tree.sync()
    daily_monitor.start()

bot.run(TOKEN)
