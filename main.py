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

# ================= 🧠 战法说明书 (V12.3 极简版 <20字) =================
def get_signal_advice(t):
    advice = ""
    # 0. 估值/事件
    if "财报" in t: advice = "财报窗口期，波动剧烈，建议避险。"
    elif "历史低位" in t: advice = "估值处历史底部，黄金坑机会。"
    elif "历史高位" in t: advice = "估值高于历史均值，需业绩消化。"
    elif "DCF 低估" in t: advice = "低于内在价值，安全边际高。"
    elif "DCF 溢价" in t: advice = "高于内在价值，透支未来预期。"
    elif "PEG 低估" in t: advice = "高增长消化估值，极具性价比。"
    elif "PEG 溢价" in t: advice = "增长跟不上股价，估值偏贵。"
    elif "PS 低估" in t: advice = "营收低估，适合亏损成长股。"
    elif "PS 溢价" in t: advice = "市销率过高，透支增长空间。"
    elif "PE 低估" in t: advice = "市盈率处于低位，价格便宜。"
    elif "PE 溢价" in t: advice = "市盈率处于高位，情绪溢价。"
    
    # 1. 形态
    elif "三角旗" in t: advice = "整理结束放量突破，主升浪开启。"
    elif "双底" in t: advice = "双底探明，突破颈线，反转确立。"
    elif "双顶" in t: advice = "双顶确立，跌破颈线，见顶信号。"
    elif "杯柄" in t: advice = "杯柄洗盘结束，大牛股启动信号。"
    elif "回踩" in t: advice = "缩量回踩均线不破，最佳买点。"
    elif "三线打击" in t: advice = "大阳吞没三阴，罕见暴力反转。"
    elif "趋势线" in t: advice = "低点抬高，回踩支撑，趋势向上。"
    elif "早晨" in t or "锤子" in t: advice = "底部多头抵抗，止跌反弹信号。"
    elif "黄昏" in t or "断头" in t: advice = "顶部空头反扑，见顶回落信号。"
    elif "吞没" in t or "包" in t: advice = "反向吞没，力量逆转，变盘在即。"
    elif "跳空" in t: advice = "资金强势抢筹或出逃，动能极强。"
    elif "布林收口" in t: advice = "波动率极低，变盘在即，盯紧方向。"

    # 2. 择时
    elif "九转" in t and "买" in t: advice = "连跌九天，物极必反，博弈反弹。"
    elif "九转" in t and "卖" in t: advice = "连涨九天，动能衰竭，注意回调。"
    elif "十三转" in t: advice = "趋势衰竭至极值，变盘一触即发。"
    
    # 3. 资金
    elif "爆量" in t: advice = "巨量异动，主力大举进出。"
    elif "放量" in t: advice = "量价齐升，上涨健康，趋势向好。"
    elif "缩量" in t: advice = "缩量洗盘或背离，关注变盘。"
    elif "VWAP 站上" in t: advice = "站上机构成本线，日内多头主导。"
    elif "VWAP 跌破" in t: advice = "跌破机构成本线，日内空头主导。"
    
    # 4. 趋势
    elif "Supertrend 看多" in t: advice = "站稳止损线，趋势向上，持股。"
    elif "Supertrend 看空" in t: advice = "跌破止损线，趋势转空，离场。"
    elif "Nx 牛市" in t: advice = "价格沿上升通道运行，持股待涨。"
    elif "Nx 熊市" in t: advice = "价格受下降通道压制，反弹即卖。"
    elif "Nx" in t: advice = "通道震荡，关注突破方向。"
    elif "ADX" in t: advice = "趋势强度走高，单边行情加速。"
    elif "多头" in t: advice = "均线发散向上，最强多头趋势。"
    elif "空头" in t: advice = "均线发散向下，最弱空头趋势。"
    
    # 5. 摆动
    elif "背离" in t: advice = "价格与指标背离，动能衰竭。"
    elif "反钩" in t: advice = "超跌后极值反弹，短线金叉。"
    elif "超买" in t: advice = "情绪过热，勿追高，防回调。"
    elif "超卖" in t: advice = "情绪冰点，勿杀跌，博反弹。"
    
    return advice

# ================= ⚖️ 评分系统 =================
def get_signal_category_and_score(s):
    s = s.strip()
    
    # 0. 估值/事件
    if "财报" in s: return 'fundamental', 0 
    if "历史" in s: return 'fundamental', 3 if "低位" in s else 0
    if "DCF" in s: return 'fundamental', 2 if "低估" in s else 0
    if "PEG" in s: return 'fundamental', 2 if "低估" in s else 0
    if "PS" in s: return 'fundamental', 2 if "低估" in s else 0
    if "PE" in s: return 'fundamental', 2 if "低估" in s else 0

    # 1. 形态/支撑 (Pattern)
    if "三线打击" in s: return 'pattern', 5
    if "双底" in s or "杯柄" in s or "三角旗突破" in s: return 'pattern', 4
    if "双顶" in s or "三角旗跌破" in s: return 'pattern', -4
    if "回踩" in s or "趋势线" in s or "布林收口" in s: return 'pattern', 3
    if "跳空" in s: return 'pattern', 3 if "上" in s else -3
    if any(x in s for x in ["早晨", "阳包阴", "锤子"]): return 'pattern', 4
    if any(x in s for x in ["断头", "阴包阳", "射击", "黄昏", "墓碑"]): return 'pattern', -4

    # 2. 择时
    if "九转" in s or "十三转" in s:
        return 'timing', 4 if ("买入" in s or "底部" in s) else -4
        
    # 3. 资金
    if "VWAP" in s: return 'volume', 2 if "站上" in s else -2
    if "盘中爆量" in s: return 'volume', 4 if "抢筹" in s else -4
    if "放量" in s: return 'volume', 3 if "大涨" in s else -3
    if "缩量" in s: return 'volume', 1 if "回调" in s else -1
    
    # 4. 趋势
    if "Supertrend" in s: return 'trend', 3 if "看多" in s else -3
    if "ADX" in s: return 'trend', 1
    if any(x in s for x in ["多头", "年线", "唐奇安上"]): return 'trend', 3
    if any(x in s for x in ["空头", "年线", "唐奇安下"]): return 'trend', -3
    if any(x in s for x in ["Nx 突破", "Nx 站稳", "Nx 牛市", "R1"]): return 'trend', 2
    if any(x in s for x in ["Nx 跌破", "Nx 熊市", "S1"]): return 'trend', -2
    if "站上" in s: return 'trend', 1
    if "跌破" in s: return 'trend', -1
    
    # 5. 摆动
    if "背离" in s: return 'oscillator', 3 if "底" in s else -3
    if "反钩" in s: return 'oscillator', 2
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
    earnings_blocks = [] 
    active_list = []     
    inactive_lines = []  
    
    for item in items:
        score_val = item['score']
        score_str = f"+{score_val}" if score_val > 0 else f"{score_val}"
        
        if item['active']:
            total_score += score_val
            block = f"### {item['raw']} ({score_str})"
            advice = get_signal_advice(item['raw'])
            if advice: block += f"\n> {advice}"
            
            if "财报" in item['raw']:
                icon = "### 🚨 " if "高危" in item['raw'] else "### ⚠️ "
                block = block.replace("### ", icon)
                earnings_blocks.append(block)
            
            elif score_val == 0:
                block = f"ℹ️ **{item['raw']}**"
                if advice: block += f"\n> {advice}"
                active_list.append({'block': block, 'score': 0})
                
            else:
                active_list.append({'block': block, 'score': score_val})
        else:
            if score_val != 0:
                inactive_lines.append(f"🔸 {item['raw']} ({score_str}) [已去重]")

    active_list.sort(key=lambda x: abs(x['score']) if x['score'] != 0 else -1, reverse=True)
    
    final_blocks = earnings_blocks + [x['block'] for x in active_list]
    final_text = "\n".join(final_blocks)
    if inactive_lines: 
        final_text += "\n\n" + "\n".join(inactive_lines)
        
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

def get_valuation_and_earnings(ticker, current_price):
    if not FMP_API_KEY: return []
    sigs = []
    
    try:
        # 1. 📅 财报
        today = datetime.date.today()
        future_str = (today + datetime.timedelta(days=14)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')
        cal_url = f"https://financialmodelingprep.com/stable/earnings-calendar?from={today_str}&to={future_str}&apikey={FMP_API_KEY}"
        cal_resp = requests.get(cal_url, timeout=10)
        if cal_resp.status_code == 200:
            cal_data = cal_resp.json()
            for entry in cal_data:
                if ticker == entry.get('symbol'):
                    d_str = entry.get('date')
                    if d_str:
                        diff = (parser.parse(d_str).date() - today).days
                        if 0 <= diff <= 14: sigs.append(f"财报预警 (T-{diff}天)")
                        break 

        # 2. 估值
        r_url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={FMP_API_KEY}"
        r_resp = requests.get(r_url, timeout=10)
        
        current_pe = None; current_ps = None; current_peg = None; eps_ttm = 0
        
        if r_resp.status_code == 200:
            r_data = r_resp.json()
            if r_data:
                rd = r_data[0]
                current_pe = rd.get('priceToEarningsRatioTTM')
                current_ps = rd.get('priceToSalesRatioTTM')
                current_peg = rd.get('priceToEarningsGrowthRatioTTM')
                eps_ttm = rd.get('netIncomePerShareTTM', 0)

        # 3. 历史估值 (3 Year)
        h_url = f"https://financialmodelingprep.com/stable/ratios?symbol={ticker}&limit=3&apikey={FMP_API_KEY}"
        h_resp = requests.get(h_url, timeout=10)
        avg_pe = 0; avg_ps = 0
        if h_resp.status_code == 200:
            h_data = h_resp.json()
            if h_data:
                pe_list = [x.get('priceToEarningsRatio', 0) for x in h_data if x.get('priceToEarningsRatio', 0) > 0]
                ps_list = [x.get('priceToSalesRatio', 0) for x in h_data if x.get('priceToSalesRatio', 0) > 0]
                if pe_list: avg_pe = sum(pe_list) / len(pe_list)
                if ps_list: avg_ps = sum(ps_list) / len(ps_list)

        if eps_ttm > 0:
            if current_peg is not None:
                if 0 < current_peg < 1.3: sigs.append(f"PEG 低估: {current_peg:.2f}")
                elif current_peg > 3.5: sigs.append(f"PEG 溢价: {current_peg:.2f}")
            if current_pe is not None and avg_pe > 0:
                if current_pe < avg_pe * 0.8: sigs.append(f"PE 历史低位: {current_pe:.1f} [均值 {avg_pe:.1f}]")
                elif current_pe > avg_pe * 1.3: sigs.append(f"PE 历史高位: {current_pe:.1f} [均值 {avg_pe:.1f}]")
        else:
            if current_ps is not None and avg_ps > 0:
                if current_ps < avg_ps * 0.8: sigs.append(f"PS 历史低位: {current_ps:.2f} [均值 {avg_ps:.2f}]")
                elif current_ps > avg_ps * 1.3: sigs.append(f"PS 历史高位: {current_ps:.2f} [均值 {avg_ps:.2f}]")

        # 4. DCF
        d_url = f"https://financialmodelingprep.com/stable/discounted-cash-flow?symbol={ticker}&apikey={FMP_API_KEY}"
        d_resp = requests.get(d_url, timeout=10)
        if d_resp.status_code == 200:
            d_data = d_resp.json()
            if d_data and 'dcf' in d_data[0]:
                dcf = d_data[0]['dcf']
                if dcf > 0:
                    if current_price < dcf * 0.85: sigs.append(f"DCF 低估: ${dcf:.1f}")
                    elif current_price > dcf * 2.0: sigs.append(f"DCF 溢价: ${dcf:.1f}")

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

def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 50: return None, None
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
    try: df.ta.supertrend(length=10, multiplier=3, append=True)
    except: pass
    try: df.ta.kdj(length=9, signal=3, append=True)
    except: pass
    try: df.ta.adx(length=14, append=True)
    except: pass
    try: df.ta.vwap(append=True)
    except: pass
    
    df.ta.willr(length=14, append=True); df.ta.cci(length=20, append=True)
    df.ta.obv(append=True)
    df.ta.atr(length=14, append=True); df.ta.donchian(lower_length=20, upper_length=20, append=True)
    try: df.ta.pivots(type="fibonacci", append=True)
    except: pass
    
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.columns = [str(c).upper() for c in df.columns]

    curr = df.iloc[-1]; prev = df.iloc[-2]; 
    price = curr['CLOSE']

    # 0. 估值
    val_sigs = get_valuation_and_earnings(ticker, price)
    signals.extend(val_sigs)

    # 1. 机构/资金
    if 'VWAP_D' in df.columns:
        if curr['CLOSE'] > curr['VWAP_D']: signals.append("VWAP 站上")
        else: signals.append("VWAP 跌破")
    
    vol_ma = curr['VOL_MA_20']
    if pd.notna(vol_ma) and vol_ma > 0:
        rvol = curr['VOLUME'] / vol_ma
        if rvol > 2.0 and curr['CLOSE'] > prev['CLOSE']: signals.append(f"盘中爆量抢筹 (量比:{rvol:.1f}x)")
        elif rvol > 1.5 and curr['CLOSE'] > prev['CLOSE']: signals.append(f"放量大涨 (量比:{rvol:.1f}x)")
        elif rvol < 0.6 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"缩量回调 (量比:{rvol:.1f}x)")

    # 2. 形态
    ret_20 = (curr['CLOSE'] - df['CLOSE'].iloc[-21]) / df['CLOSE'].iloc[-21]
    high_10 = df['HIGH'].iloc[-11:-1].max()
    if ret_20 > 0.10 and curr['CLOSE'] > high_10 and curr['VOLUME'] > vol_ma * 1.5:
        signals.append("🏴 三角旗形突破")
    
    low_10 = df['LOW'].iloc[-11:-1].min()
    if ret_20 < -0.10 and curr['CLOSE'] < low_10 and curr['VOLUME'] > vol_ma * 1.5:
        signals.append("🏴 三角旗形跌破")

    if (df['CLOSE'].iloc[-2] < df['OPEN'].iloc[-2]) and \
       (df['CLOSE'].iloc[-3] < df['OPEN'].iloc[-3]) and \
       (df['CLOSE'].iloc[-4] < df['OPEN'].iloc[-4]) and \
       (curr['CLOSE'] > curr['OPEN']) and \
       (curr['CLOSE'] > df['OPEN'].iloc[-4]) and \
       (curr['OPEN'] < df['CLOSE'].iloc[-2]):
        signals.append("💂‍♂️ 三线打击 (暴力反转)")

    try:
        recent_60 = df.iloc[-60:]
        l1 = recent_60['LOW'].iloc[:30].min()
        l2 = recent_60['LOW'].iloc[30:].min()
        neck_w = recent_60['HIGH'].iloc[15:45].max()
        if abs(l1 - l2) / l1 < 0.05 and curr['CLOSE'] > neck_w and curr['CLOSE'] > curr['OPEN']:
            signals.append("🇼 双底突破 (W-Bottom)")
        year_high = df['HIGH'].iloc[-250:].max()
        if curr['CLOSE'] > year_high * 0.95 and 60 < curr['RSI_14'] < 75:
            signals.append("☕ 杯柄形态突破")
    except: pass

    bbu_col = 'BBU_20_2.0' if 'BBU_20_2.0' in df.columns else 'BBU_20_2'
    bbl_col = 'BBL_20_2.0' if 'BBL_20_2.0' in df.columns else 'BBL_20_2'
    if bbu_col in df.columns and bbl_col in df.columns and 'SMA_20' in df.columns:
        try:
            bw = (curr[bbu_col] - curr[bbl_col]) / curr['SMA_20']
            min_bw_20 = ((df[bbu_col] - df[bbl_col]) / df['SMA_20']).iloc[-20:].min()
            if bw <= min_bw_20 * 1.05: signals.append("🤐 布林收口 (变盘前夜)")
        except: pass
    
    if curr['LOW'] > prev['HIGH']: signals.append("🕳️ 向上跳空 (缺口不补)")

    ma_list = [10, 20, 50, 100, 200]
    bounce_found = False
    for m in ma_list:
        ma_col = f'SMA_{m}'
        if ma_col in df.columns:
            ma_val = curr[ma_col]
            if (curr['LOW'] <= ma_val * 1.015) and (curr['CLOSE'] > ma_val):
                signals.append(f"回踩 MA{m} 获支撑")
                bounce_found = True
    if not bounce_found:
        if df['LOW'].iloc[-1] > df['LOW'].iloc[-2] > df['LOW'].iloc[-3]:
             if curr['CLOSE'] > curr['OPEN']: signals.append("趋势线支撑 (Higher Lows)")
    
    # 3. 九转/十三转
    try:
        work_df = df.iloc[-50:].copy()
        c = work_df['CLOSE'].values
        buy_setup = 0; sell_setup = 0
        for i in range(4, len(c)):
            if c[i] > c[i-4]: sell_setup += 1; buy_setup = 0
            elif c[i] < c[i-4]: buy_setup += 1; sell_setup = 0
            else: buy_setup = 0; sell_setup = 0
        if buy_setup == 9: signals.append("神奇九转: 底部买入信号 (9)")
        elif sell_setup == 9: signals.append("神奇九转: 顶部卖出信号 (9)")
        if buy_setup == 13: signals.append("迪玛克十三转: 终极底部 (13)")
        elif sell_setup == 13: signals.append("迪玛克十三转: 终极顶部 (13)")
    except: pass

    # 4. 趋势
    st_col = 'SUPERT_10_3.0' if 'SUPERT_10_3.0' in df.columns else 'SUPERT_10_3'
    if st_col in df.columns:
        if curr['CLOSE'] > curr[st_col]: signals.append("Supertrend 看多")
        else: signals.append("Supertrend 看空")

    has_trend = False
    if 'ADX_14' in df.columns and curr['ADX_14'] > 20:
        has_trend = True
        signals.append(f"ADX 趋势加速 ({curr['ADX_14']:.1f})")

    if any(x in ["多头", "年线"] for x in signals) and not has_trend: pass 
    else:
        if (curr['SMA_5'] > curr['SMA_10'] > curr['SMA_20'] > curr['SMA_60']): signals.append("均线多头排列")
        if (curr['SMA_5'] < curr['SMA_10'] < curr['SMA_20'] < curr['SMA_60']): signals.append("均线空头排列")

    if curr['CLOSE'] > curr['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_YELL_UP']:
        if prev['CLOSE'] < prev['NX_BLUE_UP']: signals.append("Nx 突破双梯")
        elif curr['CLOSE'] > curr['NX_BLUE_DW']: signals.append("Nx 站稳蓝梯")
    if curr['NX_BLUE_DW'] > curr['NX_YELL_UP']: signals.append("Nx 牛市排列")
    elif curr['NX_YELL_DW'] > curr['NX_BLUE_UP']: signals.append("Nx 熊市压制")
    
    # 5. 摆动
    if 'J_9_3' in df.columns:
        if prev['J_9_3'] < 0 and curr['J_9_3'] > prev['J_9_3']: signals.append("J值反钩 (超跌反弹)")
    
    if curr['RSI_14'] > 75: signals.append(f"RSI 超买 ({curr['RSI_14']:.1f})")
    elif curr['RSI_14'] < 30: signals.append(f"RSI 超卖 ({curr['RSI_14']:.1f})")
    
    return price, signals

# ================= Bot 指令集 =================
@bot.event
async def on_ready():
    load_data()
    print(f'✅ V12.3 极简文案版Bot已启动: {bot.user}')
    await bot.tree.sync()
    if not daily_monitor.is_running(): daily_monitor.start()

@bot.tree.command(name="help_bot", description="显示指令手册")
async def help_bot(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 指令手册 (V12.3)", color=discord.Color.blue())
    embed.add_field(name="🔒 隐私说明", value="您添加的列表仅自己可见，Bot会单独艾特您推送。", inline=False)
    embed.add_field(name="📋 监控", value="`/add [代码]` : 添加自选\n`/remove [代码]` : 删除自选\n`/list` : 查看我的列表", inline=False)
    embed.add_field(name="🔎 临时查询", value="`/check [代码]` : 立刻分析", inline=False)
    embed.set_footer(text="FMP Ultimate API • 机构级多因子模型")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="check", description="立刻分析股票")
@app_commands.describe(tickers="输入股票代码，多个代码用空格分开 (如: TSLA AAPL)")
async def check_stocks(interaction: discord.Interaction, tickers: str):
    await interaction.response.defer()
    stock_list = tickers.upper().replace(',', ' ').split()[:5]
    for ticker in stock_list:
        try:
            price, signals = analyze_daily_signals(ticker)
            if price is None:
                await interaction.followup.send(f"❌ 无法获取 {ticker} 数据 (可能是代码错误或FMP无数据)")
                continue
            if not signals: signals.append("趋势平稳，暂无异动")
            
            score, desc_final = generate_report_content(signals)
            text_part, color = format_dashboard_title(score)
            
            embed = discord.Embed(title=f"{ticker} : {text_part}", description=f"**现价**: ${price:.2f}\n\n{desc_final}", color=color)
            embed.set_image(url=get_finviz_chart_url(ticker))
            
            ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
            embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"⚠️ 分析 {ticker} 时发生错误: {e}")

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
    
    ny_now_str = datetime.datetime.now(ny_tz).strftime('%H:%M')

    for user_id, stocks in watch_data.items():
        user_alerts = []
        for ticker, data in stocks.items():
            try:
                price, signals = analyze_daily_signals(ticker)
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
                        embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_now_str}")
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
