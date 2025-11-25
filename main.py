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
import re
from dateutil import parser

# ================= 配置区域 =================
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY')

BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist_v29_9.json")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

watch_data = {}

# ================= 战法说明书 =================
SIGNAL_COMMENTS = {
    "机构满仓": "资金疯狂涌入，主力隐秘抢筹。",
    "机构抛售": "资金疯狂出逃，毁灭性抛压。",
    "主力吸筹": "资金逆势流入，底部构筑。",
    "主力派发": "股价涨资金流出，诱多风险。",
    "爆量抢筹": "阳线放巨量，真金白银进场。",
    "爆量出货": "阴线放巨量，主力大举出逃。",
    "Supertrend 看多": "站稳止损线，趋势向上。",
    "Supertrend 看空": "跌破止损线，趋势转空。",
    "云上金叉": "突破云层压力，真趋势确立。",
    "云下死叉": "被云层压制，空头趋势延续。",
    "站上云层": "多头突破阻力，趋势转强。",
    "跌破云层": "支撑失效，下方空间打开。",
    "通道有效突破": "放量突破盘整，单边开启。",
    "通道有效跌破": "放量跌破盘整，加速下跌。",
    "三线打击": "大阳吞没三阴，暴力反转。",
    "双底结构": "W底结构确认，颈线突破。",
    "RSI 底背离": "股价新低指标未新低，反弹酝酿。",
    "黄金坑": "戴维斯双击：高盈利+低估值。",
    "九转": "情绪极值，变盘在即。",
    "锤子线": "低位长下影，资金尝试承接。",
    "早晨之星": "低位K线组合，黎明前的黑暗。",
    "恐慌极值": "带血筹码涌出，往往见底。",
    "价值陷阱": "公司亏损 (EPS<0)，估值失效。",
    "财报预警": "财报窗口期波动剧烈，建议观望。"
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
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                watch_data = json.load(f)
        except:
            watch_data = {}
    else:
        save_data()

def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(watch_data, f, indent=4, ensure_ascii=False)
    except:
        pass

# ================= V29.9 终极评分系统 =================
def get_signal_score(s, regime="TREND"):
    s = s.strip()
    if "💡" in s: return 0.0

    if "云上金叉" in s: return 3.3
    if "云下死叉" in s: return -3.3
    if "CMF 机构满仓 (极强)" in s: return 3.1
    if "CMF 机构抛售 (极强)" in s: return -3.1
    if "三线打击" in s: return 3.0
    if "爆量抢筹" in s: return 2.8
    if "爆量出货" in s: return -3.0
    if "双底结构" in s: return 2.8
    if "黄金坑" in s: return 2.6
    if "九转: 底部买入信号" in s: return 2.5
    if "九转: 顶部卖出信号" in s: return -2.7

    if "Supertrend 看多" in s: return 1.7
    if "Supertrend 看空" in s: return -1.7
    if "一目均衡: 站上云层" in s: return 1.6
    if "一目均衡: 跌破云层" in s: return -1.6
    if "CMF 主力吸筹" in s: return 1.6
    if "CMF 主力派发" in s: return -1.6
    if "肯特纳: 通道有效突破" in s: return 1.8
    if "肯特纳: 通道有效跌破" in s: return -1.8
    if "回踩 MA20 获支撑" in s: return 1.2
    if "RSI 底背离 (抄底)" in s: return 1.5
    if "RSI 顶背离 (离场)" in s: return -1.5
    if "放量大涨" in s: return 1.0
    if "放量杀跌" in s: return -1.3
    if "缩量上涨" in s: return -1.0
    if "量: 缩量回调" in s: return 0.6
    if "价值陷阱" in s: return -2.0

    return 0

def generate_report_content(signals, regime="TREND"):
    items = []
    raw_score = 0.0
    has_bottom_signal = False
    bottom_keywords = ["黄金坑", "底背离", "九转: 底部", "锤子", "早晨之星", "双底结构", "恐慌极值"]

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
        if "财报预警" in item['raw']:
            if not earnings_shown:
                final_blocks.insert(0, f"### ⚠️ {item['raw']}\n> 财报窗口期，波动加剧，建议观望")
                earnings_shown = True
            continue

        score_val = item['score']
        if score_val == 0:
            title = f"### {item['raw']}"
        else:
            score_str = f"+{score_val:.1f}" if score_val > 0 else f"{score_val:.1f}"
            title = f"### {item['raw']} ({score_str})"

        if abs(score_val) >= 0.5 or "💡" in item['raw']:
            comment = get_comment(item['raw'])
            if comment:
                final_blocks.append(f"{title}\n> {comment}")
            else:
                final_blocks.append(title)

    final_text = "\n".join(final_blocks)
    main_reasons = [x['raw'] for x in items if abs(x['score']) >= 1.5 or "💡" in x['raw']][:3]
    return raw_score, final_text, main_reasons, has_bottom_signal

def format_dashboard_title(score, has_bottom_signal=False):
    base = abs(score)
    full = int(base)
    half = "✨" if base - full >= 0.5 else ""
    stars = "⭐" * min(full, 10) + half
    skulls = "💀" * min(full, 10)
    icons = stars if score > 0 else skulls if score < 0 else "⚖️"

    if score >= 9.0:
        status, color = "机构狂买", discord.Color.from_rgb(0,255,0)
    elif score >= 6.5:
        status, color = "多头总攻", discord.Color.green()
    elif score >= 3.5:
        status, color = "确定性多头", discord.Color.blue()
    elif score >= 1.0:
        status, color = "趋势看多", discord.Color.teal()
    elif score > -1.0:
        status, color = "多空平衡", discord.Color.gold()
    elif score > -3.5:
        status, color = "轻微偏空", discord.Color.orange()
    else:
        status, color = "机构出逃", discord.Color.red()

    if has_bottom_signal and score < 5.0:
        status += " 抄底雷达"

    advice = "【满仓冲】" if score >= 9 else \
             "【80%仓】" if score >= 6.5 else \
             "【50%仓】" if score >= 3.5 else \
             "【30%仓】" if score >= 1.0 else \
             "【空仓观望】" if -1 < score < 1 else \
             "【减仓】" if score > -3.5 else "【清仓】"

    return f"{status} ({score:+.1f}) {icons}", color, advice

# ================= 数据获取 =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def get_valuation_and_earnings(ticker, current_price):
    if not FMP_API_KEY: return []
    sigs = []
    try:
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
                        if 0 <= diff <= 14:
                            sigs.append(f"财报预警 [T-{diff}天]")
                    break

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
                        pe_list = [x.get('priceToEarningsRatio', 0) for x in h_data if x.get('priceToEarningsRatio', 0) > 0]
                        if pe_list:
                            avg_pe = sum(pe_list)/len(pe_list)
                            if pe and pe < avg_pe * 0.8:
                                sigs.append(f"黄金坑 (历史低位) [PE:{pe:.1f}]")
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
            df.loc[idx, ['close', 'high', 'low', 'volume']] = [curr['price'], max(df.loc[idx, 'high'], curr['price']), min(df.loc[idx, 'low'], curr['price']), curr.get('volume', df.loc[idx, 'volume'])]
        else:
            new_row = {'date': today_str, 'open': curr.get('open', df['close'].iloc[-1]), 'high': curr.get('dayHigh', curr['price']), 'low': curr.get('dayLow', curr['price']), 'close': curr['price'], 'volume': curr.get('volume', 0)}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        print(f"Data error for {ticker}: {e}")
        return None

# ================= 核心分析函数 =================
def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 100: return None, None, None, None

    df.columns = [str(c).upper() for c in df.columns]
    signals = []

    df.ta.supertrend(length=10, multiplier=3, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.aroon(length=25, append=True)
    df.ta.cmf(length=20, append=True)
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.ta.kc(length=20, scalar=2, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    try: df.ta.cdl_pattern(name=["hammer", "morningstar"], append=True)
    except: pass

    high9 = df['HIGH'].rolling(9).max(); low9 = df['LOW'].rolling(9).min()
    df['tenkan'] = (high9 + low9) / 2
    high26 = df['HIGH'].rolling(26).max(); low26 = df['LOW'].rolling(26).min()
    df['kijun'] = (high26 + low26) / 2
    high52 = df['HIGH'].rolling(52).max(); low52 = df['LOW'].rolling(52).min()
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    df['senkou_b'] = ((high52 + low52) / 2).shift(26)

    curr = df.iloc[-1]; prev = df.iloc[-2]; price = curr['CLOSE']
    market_regime = "TREND" if (curr.get('ADX_14', 0) > 25) else "RANGE"

    signals.extend(get_valuation_and_earnings(ticker, price))

    st_cols = [c for c in df.columns if c.startswith('SUPERT')]
    st_col = st_cols[0] if st_cols else None
    is_bull = False
    if st_col and pd.notna(curr[st_col]):
        if curr['CLOSE'] > curr[st_col]:
            signals.append("Supertrend 看多")
            is_bull = True
        else:
            signals.append("Supertrend 看空")

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

    if 'CMF_20' in df.columns:
        cmf = curr['CMF_20']
        if cmf > 0.25: signals.append(f"CMF 机构满仓 (极强) [{cmf:.2f}]")
        elif cmf < -0.25: signals.append(f"CMF 机构抛售 (极强) [{cmf:.2f}]")
        elif cmf > 0.20: signals.append(f"CMF 主力吸筹 (强) [{cmf:.2f}]")
        elif cmf < -0.20: signals.append(f"CMF 主力派发 (强) [{cmf:.2f}]")

    vol_ma = curr.get('VOL_MA_20', 0)
    rvol = curr['VOLUME'] / vol_ma if pd.notna(vol_ma) and vol_ma > 0 else 1
    is_green = curr['CLOSE'] > curr['OPEN']
    if rvol > 2.0:
        signals.append(f"量: 爆量抢筹 [量比:{rvol:.1f}x]" if is_green else f"量: 爆量出货 [量比:{rvol:.1f}x]")
    elif rvol > 1.5:
        signals.append(f"量: 放量大涨 [量比:{rvol:.1f}x]" if curr['CLOSE'] > prev['CLOSE'] else f"量: 放量杀跌 [量比:{rvol:.1f}x]")

    kc_up = [c for c in df.columns if c.startswith('KCU')][0] if any(c.startswith('KCU') for c in df.columns) else None
    kc_low = [c for c in df.columns if c.startswith('KCL')][0] if any(c.startswith('KCL') for c in df.columns) else None
    adx_val = curr.get('ADX_14', 0)
    if kc_up and price > curr[kc_up] and adx_val > 20 and rvol > 1.0:
        signals.append("肯特纳: 通道有效突破")
    if kc_low and price < curr[kc_low] and adx_val > 20 and rvol > 1.0:
        signals.append("肯特纳: 通道有效跌破")

    if curr['RSI_14'] < 20 and rvol > 1.5:
        signals.append("💡 恐慌极值 (带血筹码)")
    if curr['RSI_14'] < 40:
        if any('HAMMER' in c for c in df.columns) and df.filter(like='HAMMER').iloc[-1].item() != 0:
            signals.append("💡 K线: 锤子线 (低位探底)")
        if any('MORNINGSTAR' in c for c in df.columns) and df.filter(like='MORNINGSTAR').iloc[-1].item() != 0:
            signals.append("💡 K线: 早晨之星 (低位反转)")

    try:
        ma200 = df['CLOSE'].rolling(200).mean().iloc[-1]
        if price < ma200 * 1.1:
            lows = df['LOW'].iloc[-60:]
            min1, min2 = lows.iloc[:30].min(), lows.iloc[30:].min()
            if abs(min1 - min2) < min1 * 0.03 and price > min1 * 1.05:
                signals.append("🇼 双底结构")
    except: pass

    if (df['CLOSE'].iloc[-4:-1].lt(df['OPEN'].iloc[-4:-1]).all() and
        curr['CLOSE'] > curr['OPEN'] and
        curr['CLOSE'] > df['OPEN'].iloc[-4]):
        signals.append("💂‍♂️ 三线打击")

    if is_bull and curr['LOW'] <= df['CLOSE'].rolling(20).mean().iloc[-1] * 1.015 and curr['CLOSE'] > df['CLOSE'].rolling(20).mean().iloc[-1]:
        signals.append("回踩 MA20 获支撑")

    c = df['CLOSE'].values[-100:]
    buy_s = sell_s = 0
    for i in range(4, len(c)):
        if c[i] > c[i-4]: sell_s += 1; buy_s = 0
        elif c[i] < c[i-4]: buy_s += 1; sell_s = 0
        else: buy_s = sell_s = 0
    if buy_s >= 9: signals.append("九转: 底部买入信号 [9]")
    if sell_s >= 9: signals.append("九转: 顶部卖出信号 [9]")

    # 终极止损
    atr_val = curr.get('ATRr_14', curr.get('ATR_14', 0))
    final_stop = price
    if st_col and pd.notna(curr[st_col]):
        st_price = curr[st_col]
        if is_bull:
            final_stop = max(st_price * 0.985, price - 2.8 * atr_val)
            signals.append(f"🚨 动态止损: ${final_stop:.2f} (Supertrend 主导)")
        else:
            final_stop = price + 2.8 * atr_val
            signals.append(f"🚨 动态止损: ${final_stop:.2f} (ATR 牵引)")
    else:
        final_stop = price - 2.8 * atr_val if is_bull else price + 2.8 * atr_val
        signals.append(f"🚨 动态止损: ${final_stop:.2f} (纯ATR)")

    return price, signals, market_regime, final_stop

# ================= Bot 指令 =================
@bot.tree.command(name="check", description="机构终极分析")
async def check_stocks(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    t = ticker.split()[0].replace(',', '').upper()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, analyze_daily_signals, t)
    price, signals, regime, stop_loss = result if result[0] else (None, None, None, None)

    if price is None:
        return await interaction.followup.send(f"❌ 数据获取失败: {t}")
    if not signals: signals = ["多空平衡"]

    score, desc, _, has_bottom = generate_report_content(signals, regime)
    title, color, advice = format_dashboard_title(score, has_bottom)
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')

    embed = discord.Embed(title=f"{t} : {title}", color=color)
    embed.description = f"**现价**: ${price:.2f} | **止损**: ${stop_loss:.2f}\n{advice}\n\n{desc}"
    embed.set_image(url=get_finviz_chart_url(t))
    embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
    await interaction.followup.send(embed=embed)

@tasks.loop(time=datetime.time(hour=16, minute=1, tzinfo=pytz.timezone('America/New_York')))
async def daily_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel or not watch_data: return
    today = datetime.date.today().strftime('%Y-%m-%d')
    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    loop = asyncio.get_running_loop()

    for uid, stocks in watch_data.items():
        alerts = []
        tickers = list(stocks.keys())
        results = await asyncio.gather(*[loop.run_in_executor(None, analyze_daily_signals, t) for t in tickers], return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception): continue
            price, signals, regime, stop = result
            if not price or not signals: continue

            t = tickers[i]
            stock_data = stocks[t]
            score, desc, _, has_bottom = generate_report_content(signals, regime)

            old_score = stock_data.get('last_score', 0)
            score_change = score - old_score
            should_alert = False

            if stock_data['mode'] == 'always':
                should_alert = True
            elif stock_data['last_alert_date'] != today:
                if (abs(score) >= 6.0 or abs(score_change) >= 2.8 or
                    (score >= 5.0 and old_score < 3.0) or
                    (score <= -4.0 and old_score > -2.0) or
                    has_bottom):
                    should_alert = True

            if should_alert:
                stock_data['last_alert_date'] = today
                stock_data['last_score'] = score
                title, color, advice = format_dashboard_title(score, has_bottom)
                emb = discord.Embed(title=f"{t}: {title}", color=color)
                emb.description = f"${price:.2f} | 止损 ${stop:.2f}\n{advice}\n\n{desc}"
                emb.set_image(url=get_finviz_chart_url(t))
                emb.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
                alerts.append(emb)

        if alerts:
            save_data()
            await channel.send(f"🔔 <@{uid}> 机构猎手日报已送达")
            for emb in alerts:
                await channel.send(embed=emb)
                await asyncio.sleep(1.5)

@bot.event
async def on_ready():
    load_data()
    print("✅ 机构猎手 V29.9 终极日报版启动成功")
    await bot.tree.sync()
    daily_monitor.start()

bot.run(TOKEN)
