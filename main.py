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

# ================= 📖 战法说明书 =================
SIGNAL_COMMENTS = {
    # --- 资金 (最强权重) ---
    "机构满仓": "主力资金不计成本抢筹，主升浪特征。",
    "机构抛售": "主力资金大举出逃，毁灭性抛压。",
    "主力吸筹": "股价未动资金先行，隐蔽建仓。",
    "主力派发": "股价上涨但资金流出，典型的诱多。",
    "爆量抢筹": "巨量阳线，多头情绪宣泄，强力买入。",
    "爆量出货": "巨量阴线，恐慌盘与主力砸盘共振。",
    "放量大涨": "量价齐升，主力资金入场扫货。",
    "放量杀跌": "恐慌盘涌出，承接无力，趋势破坏。",
    "放量滞涨": "量大不涨，主力可能在高位出货。",
    
    # --- 趋势 (中等权重) ---
    "Supertrend 看多": "站稳趋势线，右侧持仓信号。",
    "Supertrend 看空": "跌破趋势线，止损离场信号。",
    "云上金叉": "一目均衡表最强买点，趋势确立。",
    "云下死叉": "一目均衡表最强卖点，深不见底。",
    "站上云层": "多头突破长期阻力，阻力变支撑。",
    "跌破云层": "长期支撑失效，下方空间打开。",
    "Aroon 强多": "多头完全主导市场，单边行情。",
    
    # --- 动能 & 结构 ---
    "通道有效突破": "Keltner通道被强力突破，波动率爆发。",
    "通道有效跌破": "Keltner通道向下击穿，主跌浪加速。",
    "ADX 多头加速": "多头趋势强度持续增强，顺势加仓。",
    
    # --- 形态/反转 (左侧交易) ---
    "三线打击": "大阳线吞没连续阴线，暴力反转信号。",
    "双底结构": "W底形态构筑完成，颈线突破。",
    "双顶": "M头形态确立，上方压力沉重。",
    "RSI 底背离": "价格新低但动能衰竭，反弹一触即发。",
    "黄金坑": "戴维斯双击：高盈利增长+历史低估值。",
    "九转": "情绪达到极值，大概率发生变盘。",
    "锤子线": "低位长下影线，资金尝试承接。",
    "早晨之星": "经典的底部K线组合，黎明前的黑暗。",
    "恐慌极值": "RSI极度超卖+放量，往往是带血的筹码。",
    
    # --- 基本面 ---
    "尚未盈利": "处于成长/烧钱阶段，关注现金流与营收。",
    "价值陷阱": "基本面恶化，且技术面破位。",
    "财报": "财报窗口期波动剧烈，不确定性极高。"
}

def get_comment(raw_text):
    for key, comment in SIGNAL_COMMENTS.items():
        if key in raw_text: return comment
    return ""

# ================= 数据存取 =================
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

# ================= 🛡️ V29.5 评分逻辑 =================
def get_signal_score(s, regime="TREND"):
    s = s.strip()
    
    if "💡" in s: return 0.0 

    # --- 🔥 核心驱动 ---
    if "CMF" in s and "机构满仓" in s: return 3.5 
    if "CMF" in s and "机构抛售" in s: return -3.5
    
    if "爆量抢筹" in s: return 2.8
    if "爆量出货" in s: return -3.2

    # --- 📈 趋势结构 ---
    if "云上金叉" in s: return 3.2
    if "云下死叉" in s: return -3.2
    if "双底结构" in s: return 2.6
    if "三线打击" in s: return 2.8
    if "通道有效突破" in s: return 1.8
    if "通道有效跌破" in s: return -1.8

    # --- 💰 资金博弈 (修正量能权重) ---
    if "CMF" in s:
        if "主力吸筹" in s: return 1.6
        if "主力派发" in s: return -1.6
    
    if "量" in s:
        if "放量大涨" in s: return 1.5
        if "放量杀跌" in s: return -2.0 # 杀跌比大涨更严重
        if "放量滞涨" in s: return -0.5 # 警惕信号
        if "缩量上涨" in s: return -0.5
        if "缩量回调" in s: return 0.5

    # --- 📊 趋势跟随 ---
    if "Supertrend 看多" in s: return 1.2
    if "Supertrend 看空" in s: return -1.2
    
    if "站上云层" in s: return 1.4
    if "跌破云层" in s: return -1.4
    
    if "Aroon 强多" in s: return 0.9
    if "Aroon 强空" in s: return -0.9

    # --- 🚀 动能 ---
    if "ADX" in s:
        if "多头加速" in s: return 1.1
        if "空头加速" in s: return -1.1

    # --- 📉 摆动/反转 ---
    if "双顶" in s: return -2.2
    if "底背离" in s: return 1.5
    if "顶背离" in s: return -1.5

    if "RSI" in s:
        if "超买" in s: return -0.8 if regime == "RANGE" else 0.0
        if "超卖" in s: return 0.8 if regime == "RANGE" else 0.0

    # --- 🏦 基本面 ---
    if "尚未盈利" in s: return -1.0 
    if "价值陷阱" in s: return -2.0 
    
    if "黄金坑" in s: return 2.5
    if "九转" in s: return 1.5 if "底部" in s else -1.5
    if "华尔街" in s: return 0.5 if "买入" in s else -0.5
    if "财报" in s: return 0.0
    
    return 0.0

def generate_report_content(signals, regime="TREND"):
    items = []
    raw_score = 0.0
    has_bottom_signal = False
    
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
            score_str = f"+{score_val:.1f}" if score_val > 0 else f"{score_val:.1f}"
            title = f"### {item['raw']} ({score_str})"
        
        if abs(score_val) >= 0.8 or "💡" in item['raw'] or "盈利" in item['raw']:
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
        else:
            final_blocks.append(title)

    final_text = "\n".join(final_blocks)
    main_reasons = [x['raw'] for x in items if abs(x['score']) >= 1.2 or "财报" in x['raw']][:3]
    
    return raw_score, final_text, main_reasons, has_bottom_signal

def format_dashboard_title(score, has_bottom_signal=False):
    count = min(int(round(abs(score))), 10)
    icons = "⭐" * count if score > 0 else "💀" * count if score < 0 else "⚖️"
    
    status, color = "震荡", discord.Color.light_grey()
    pos_advice = ""
    
    if score >= 1.0: 
        if score >= 8.0: 
            status, color = "强力做多", discord.Color.from_rgb(0, 255, 0)
            pos_advice = " [建议仓位: 80%+]"
        elif score >= 4.5: 
            status, color = "积极增持", discord.Color.green()
            pos_advice = " [建议仓位: 50%]"
        else: 
            status, color = "趋势向上", discord.Color.blue()
            pos_advice = " [建议仓位: 30%]"
    elif score <= -1.0:
        if score <= -8.0: 
            status, color = "清仓离场", discord.Color.from_rgb(255, 0, 0)
            pos_advice = " [建议空仓/做空]"
        elif score <= -4.5: 
            status, color = "空头共振", discord.Color.red()
            pos_advice = " [建议减仓]"
        else: 
            status, color = "趋势向下", discord.Color.orange()
            pos_advice = " [建议减仓]"
    else:
        status, color = "多空平衡", discord.Color.gold()
        icons = "⚖️"
        pos_advice = " [建议观望]"
    
    if has_bottom_signal and score < 4.0:
        status += " (抄底机会)" 
        icons += " 🎣"
        
    return f"{status} ({score:+.1f}) {icons}", color, pos_advice

# ================= FMP API & Data Cleaning =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_valuation_and_earnings(ticker, current_price, ma200=None):
    if not FMP_API_KEY: return []
    sigs = []
    try:
        today = datetime.date.today()
        future_str = (today + datetime.timedelta(days=14)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')
        
        # 1. 财报
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
        
        # 2. 估值
        r_url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={FMP_API_KEY}"
        r_resp = requests.get(r_url, timeout=5)
        if r_resp.status_code == 200:
            r_data = r_resp.json()
            if r_data:
                rd = r_data[0]
                eps = rd.get('netIncomePerShareTTM', 0)
                pe = rd.get('priceToEarningsRatioTTM')
                
                if eps is None or eps <= 0:
                    if ma200 and current_price > ma200:
                         sigs.append("尚未盈利 (成长股/EPS<0)")
                    else:
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
        
        # ⚠️ 修正：确保时间顺序正确，防止乱序导致计算错误
        df = pd.DataFrame(hist_data)
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df['date'] = pd.to_datetime(df['date'])
        
        # 强制按照日期排序 (Oldest -> Newest)
        df.sort_values(by='date', ascending=True, inplace=True)
        
        quote_url = f"https://financialmodelingprep.com/stable/quote?symbol={ticker}&apikey={FMP_API_KEY}"
        quote_resp = requests.get(quote_url, timeout=5)
        quote_data = quote_resp.json()
        
        if quote_data:
            curr = quote_data[0]
            today_str = datetime.datetime.now().strftime('%Y-%m-%d')
            last_hist_date = df['date'].iloc[-1].strftime('%Y-%m-%d')
            
            # ⚠️ 修正：重复日期去重逻辑
            if last_hist_date == today_str:
                idx = df.index[-1]
                df.loc[idx, 'close'] = curr['price']
                df.loc[idx, 'high'] = max(df.loc[idx, 'high'], curr['price']) 
                df.loc[idx, 'low'] = min(df.loc[idx, 'low'], curr['price'])
                df.loc[idx, 'volume'] = curr.get('volume', df.loc[idx, 'volume'])
            else:
                new_row = {'date': pd.Timestamp(today_str), 'open': curr.get('open', df['close'].iloc[-1]), 'high': curr.get('dayHigh', curr['price']), 'low': curr.get('dayLow', curr['price']), 'close': curr['price'], 'volume': curr.get('volume', 0)}
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        
        # 再次去重，防止API返回重复数据
        df.drop_duplicates(subset=['date'], keep='last', inplace=True)
        df.set_index('date', inplace=True)
        
        return df
    except: return None

# ================= 📈 V29.5 核心分析逻辑 =================
def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 100: return None, None, None, None, None
    
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
    ma200 = df['CLOSE'].rolling(200).mean().iloc[-1]
    signals.extend(get_valuation_and_earnings(ticker, price, ma200))

    # 1. 趋势 (Trend)
    st_cols = [c for c in df.columns if c.startswith('SUPERT')]
    st_col = st_cols[0] if st_cols else None
    st_val = curr[st_col] if st_col else price
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

    # 2. 资金 (Volume - Fixed Logic)
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
        is_green_candle = curr['CLOSE'] > curr['OPEN']
        # ⚠️ 修正：严格基于涨跌幅判断“杀跌”还是“抢筹”
        price_change_pct = (curr['CLOSE'] - prev['CLOSE']) / prev['CLOSE']
        
        if rvol > 2.0:
            if price_change_pct > 0: signals.append(f"量: 爆量抢筹 [量比:{rvol:.1f}x]")
            else: signals.append(f"量: 爆量出货 [量比:{rvol:.1f}x]")
        elif rvol > 1.5:
            if price_change_pct > 0: signals.append(f"量: 放量大涨 [量比:{rvol:.1f}x]")
            else: signals.append(f"量: 放量杀跌 [量比:{rvol:.1f}x]")
        elif rvol < 0.8:
            if price_change_pct > 0: signals.append("量: 缩量上涨 (量价背离)")
            else: signals.append("量: 缩量回调")
        # 新增：量大但涨幅小
        elif rvol > 1.2 and abs(price_change_pct) < 0.005:
            signals.append(f"量: 放量滞涨 [量比:{rvol:.1f}x]")

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

    # ================= 风控止损 =================
    atr = curr.get('ATRr_14', 0) if 'ATRr_14' in curr else curr.get('ATR_14', 0)
    stop_long = 0
    stop_short = 0
    
    if atr > 0:
        if curr['CLOSE'] > st_val: 
            stop_long = max(st_val * 0.99, price - 2.5 * atr)
        else: 
            stop_long = price - 2.5 * atr
            
        if curr['CLOSE'] < st_val: 
            stop_short = min(st_val * 1.01, price + 2.5 * atr)
        else:
            stop_short = price + 2.5 * atr
    
    return price, signals, market_regime, stop_long, stop_short

# ================= Bot 指令 =================
@bot.tree.command(name="check", description="机构精核分析 (单只)")
async def check_stocks(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    t = ticker.split()[0].replace(',', '').upper()
    loop = asyncio.get_running_loop()
    
    price, signals, regime, s_long, s_short = await loop.run_in_executor(None, analyze_daily_signals, t)
    
    if price is None:
        return await interaction.followup.send(f"❌ 数据获取失败: {t}")
    if not signals: signals.append("多空平衡")
    
    score, desc, _, has_bottom = generate_report_content(signals, regime)
    title, color, pos_advice = format_dashboard_title(score, has_bottom)
    
    # 智能止损展示
    if score < -4.0:
        stop_val = s_short
        stop_label = "空头止损"
    else:
        stop_val = s_long
        stop_label = "多头止损"
    
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    
    embed = discord.Embed(title=f"{t} : {title}", description=f"**现价**: ${price:.2f} | {stop_label}: ${stop_val:.2f}\n{pos_advice}\n\n{desc}", color=color)
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
    for i, (p, s, r, s_long, s_short) in enumerate(results):
        t = tickers[i]
        if p is None: continue
        score, _, reasons, has_bottom = generate_report_content(s, r)
        
        raw_reason = reasons[0] if reasons else "多空平衡"
        clean_reason = re.sub(r"[\(\[].*?[\)\]]", "", raw_reason)
        clean_reason = re.sub(r"[\+\-\$\d\.\:]", "", clean_reason).strip()
        
        title, _, _ = format_dashboard_title(score, has_bottom)
        short_status = title.split(' ')[0]
        icons = title.split(' ')[2]
        if "🎣" in title and "🎣" not in icons: icons += " 🎣"
        
        lines.append(f"**{t}**: {short_status} {icons}\n└ {clean_reason}")
    
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    embed = discord.Embed(title="📊 监控面板", description="\n".join(lines), color=discord.Color.blue())
    embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="scores", description="查看V29.5评分标准")
async def show_scores(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 V29.5 机构评分表 (数据修复版)", description="强化数据清洗，精确识别放量涨跌。", color=discord.Color.gold())
    embed.add_field(name="🚀 核心驱动", value="`±3.5` CMF机构资金\n`±3.2` 云上金叉\n`+2.8` 爆量抢筹", inline=False)
    embed.add_field(name="⚖️ 量能与趋势", value="`+1.5` 放量大涨\n`-2.0` 放量杀跌\n`±1.2` Supertrend", inline=False)
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
        
        for i, (p, s, r, s_long, s_short) in enumerate(results):
            if not s: continue
            score, desc, _, has_bottom = generate_report_content(s, r)
            t = tickers[i]
            if stocks[t]['mode'] == 'always' or stocks[t]['last_alert_date'] != today:
                stocks[t]['last_alert_date'] = today
                title, color, pos_advice = format_dashboard_title(score, has_bottom)
                
                stop_val = s_long
                stop_label = "多头止损"
                if score < -4.0:
                    stop_val = s_short
                    stop_label = "空头止损"

                emb = discord.Embed(title=f"{t}: {title}", description=f"${p:.2f} | {stop_label}: ${stop_val:.2f}\n{desc}", color=color)
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
    print("✅ V29.5 机构修正版 (Data Clean + Fix Vol) 启动")
    await bot.tree.sync()
    daily_monitor.start()

bot.run(TOKEN)
