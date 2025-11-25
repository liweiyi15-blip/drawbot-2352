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
import logging
import io
import copy

# ================= 🛠️ 系统配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("bot_v33_8.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
FMP_API_KEY = os.getenv('FMP_API_KEY') 

BASE_PATH = "/data" if os.path.exists("/data") else "."
DATA_FILE = os.path.join(BASE_PATH, "watchlist_v33.json")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

watch_data = {}
api_cache_daily = {}
api_cache_fund = {}
api_cache_sector = {} 

# ================= 🗺️ 板块映射 =================
SECTOR_MAP = {
    "NVDA": "SMH", "AMD": "SMH", "AVGO": "SMH", "TSM": "SMH", "QCOM": "SMH", "MU": "SMH", "INTC": "SMH", "AMAT": "SMH", "LRCX": "SMH",
    "AAPL": "XLK", "MSFT": "XLK", "ORCL": "XLK", "ADBE": "XLK", "CRM": "XLK",
    "GOOG": "XLC", "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "TSLA": "XLY", "AMZN": "XLY", "HD": "XLY", "MCD": "XLY", "NKE": "XLY", "SBUX": "XLY",
    "JPM": "XLF", "BAC": "XLF", "V": "XLF", "MA": "XLF", "BRK.B": "XLF",
    "LLY": "XLV", "UNH": "XLV", "JNJ": "XLV", "PFE": "XLV",
    "XOM": "XLE", "CVX": "XLE",
    "LABU": "XBI", "XBI": "XBI",
    "MSTR": "IBIT", "COIN": "IBIT", "MARA": "IBIT", "IBIT": "IBIT"
}

# ================= 📖 因子字典 =================
FACTOR_COMMENTS = {
    "Trend_Bull": "趋势多头排列 (x1.3)",
    "Trend_Bear": "趋势回调，仅限极轻仓 (x0.8)", 
    "Trend_Chop": "趋势震荡整理 (x0.8)",
    "VSA_Lock": "缩量新高，主力锁仓 (x1.3)",
    "VSA_Pump": "放量上涨，资金抢筹 (x1.2)",
    "VSA_Churn": "放量滞涨，出货迹象 (x0.5)",
    "VSA_Exit": "放量+买盘枯竭，机构派发 (x0.3)",
    "VSA_Dump": "放量下跌，恐慌抛售 (x0.5)",
    "VSA_Strong": "K线强势，机构护盘 (x1.1)",
    "Fund_Fake": "☠️ 真雷伪成长 (x0.0)",
    "Fund_Growth": "🔥 成长中亏损，可极轻仓 (x0.9)", 
    "Fund_Super": "🚀 超级成长+高毛利 (x1.25)",
    "Fund_Good": "持续盈利，商业模式验证 (x1.1)",
    "Fund_Cash": "高自由现金流，现金奶牛 (x1.3)",
    "Sector_Hot": "板块强势，趋势共振 (x1.2)",
    "Sector_Cold": "板块弱势，拖累个股 (x0.9)",
    "Vol_High": "高波动率，自动降杠杆 (x0.7)",
    "Regime_Bull": "系统性牛市，基准分上调",
    "Regime_Bear": "系统性熊市，基准分下调",
    "Regime_Panic": "VIX恐慌，极端降杠杆"
}

# ================= 数据层 =================
def load_data():
    global watch_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: watch_data = json.load(f)
        except: watch_data = {}
    else: save_data()

def save_data():
    try:
        with open(DATA_FILE, 'w') as f: json.dump(watch_data, f, indent=4)
    except: pass

def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

def log_url(url, tag="API"):
    masked_url = url.replace(FMP_API_KEY, "******")
    logger.info(f"[{tag}] Request: {masked_url}")

def get_market_regime_detailed():
    if not FMP_API_KEY: return None, None, "API缺失"
    spy_trend = "Neutral"; vix_level = 0
    try:
        vix_url = f"https://financialmodelingprep.com/stable/quote?symbol=^VIX&apikey={FMP_API_KEY}"
        vix_resp = requests.get(vix_url, timeout=5).json()
        if vix_resp: vix_level = vix_resp[0].get('price', 0)

        spy_url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=SPY&apikey={FMP_API_KEY}"
        spy_resp = requests.get(spy_url, timeout=5)
        spy_data = pd.DataFrame(spy_resp.json()).iloc[:300].iloc[::-1]
        if spy_data['close'].iloc[-1] > spy_data['close'].rolling(200).mean().iloc[-1]:
            spy_trend = "Bull"
        else: spy_trend = "Bear"
        return spy_trend, vix_level, "获取成功"
    except: return "Neutral", 20, "失败"

def get_sector_momentum(ticker):
    etf = SECTOR_MAP.get(ticker, "SPY") 
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    if etf in api_cache_sector and api_cache_sector[etf]['date'] == today_str:
        return api_cache_sector[etf]['ret_20d'], etf

    if not FMP_API_KEY: return 0, etf
    try:
        url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={etf}&apikey={FMP_API_KEY}"
        resp = requests.get(url, timeout=5).json()
        df = pd.DataFrame(resp).iloc[:50]
        if len(df) > 20:
            curr = df['close'].iloc[0]; prev_20 = df['close'].iloc[20]
            ret_20d = (curr - prev_20) / prev_20
            api_cache_sector[etf] = {'date': today_str, 'ret_20d': ret_20d}
            return ret_20d, etf
    except: pass
    return 0, etf

def get_fundamentals_deep(ticker):
    if not FMP_API_KEY: return None
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    if ticker in api_cache_fund and api_cache_fund[ticker]['date'] == today_str:
        return api_cache_fund[ticker]['data']

    try:
        inc_url = f"https://financialmodelingprep.com/stable/income-statement?symbol={ticker}&limit=2&apikey={FMP_API_KEY}"
        inc_resp = requests.get(inc_url, timeout=5).json()
        ratio_url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={FMP_API_KEY}"
        ratio_resp = requests.get(ratio_url, timeout=5).json()
        
        data = {}
        if inc_resp and len(inc_resp) >= 2:
            curr_rev = inc_resp[0].get('revenue', 0); prev_rev = inc_resp[1].get('revenue', 0)
            data['rev_growth'] = (curr_rev - prev_rev) / prev_rev if prev_rev > 0 else 0
            data['eps'] = inc_resp[0].get('eps', 0)
        if ratio_resp:
            data['gross_margin'] = ratio_resp[0].get('grossProfitMarginTTM', 0.35)
            data['fcf_yield'] = ratio_resp[0].get('freeCashFlowYieldTTM', 0)
        api_cache_fund[ticker] = {'date': today_str, 'data': data}
        return data
    except: return None

def get_daily_data_stable(ticker):
    if not FMP_API_KEY: return None, None
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    if ticker in api_cache_daily and api_cache_daily[ticker]['date'] == today_str:
        return api_cache_daily[ticker]['df'].copy(), api_cache_daily[ticker]['quote']

    try:
        hist_url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={ticker}&apikey={FMP_API_KEY}"
        df = pd.DataFrame(requests.get(hist_url, timeout=10).json())
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df['date'] = pd.to_datetime(df['date']); df.sort_values(by='date', ascending=True, inplace=True)
        
        quote_url = f"https://financialmodelingprep.com/stable/quote?symbol={ticker}&apikey={FMP_API_KEY}"
        curr_quote = requests.get(quote_url, timeout=5).json()[0]
        
        if 'upVolume' in curr_quote and (curr_quote['upVolume'] == 'N/A' or curr_quote['upVolume'] is None):
            curr_quote['upVolume'] = None; curr_quote['downVolume'] = None
        
        last_hist_date = df['date'].iloc[-1].strftime('%Y-%m-%d')
        if last_hist_date == today_str:
            idx = df.index[-1]
            df.loc[idx, ['close', 'high', 'low', 'volume']] = [curr_quote['price'], max(df.loc[idx,'high'], curr_quote['price']), min(df.loc[idx,'low'], curr_quote['price']), curr_quote['volume']]
        else:
            new_row = {'date': pd.Timestamp(today_str), 'open': curr_quote['open'], 'high': curr_quote['dayHigh'], 'low': curr_quote['dayLow'], 'close': curr_quote['price'], 'volume': curr_quote['volume']}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        
        df.drop_duplicates(subset=['date'], keep='last', inplace=True)
        df.set_index('date', inplace=True)
        df.columns = [str(c).upper() for c in df.columns]
        df = df.ffill().fillna(0)
        
        api_cache_daily[ticker] = {'date': today_str, 'df': df, 'quote': curr_quote}
        return df, curr_quote
    except: return None, None

# ================= 🧠 V33.8 信念加仓引擎 =================

def calculate_v33_score(df, quote_data, fundamentals, spy_trend, vix_level, ticker):
    curr = df.iloc[-1]; prev = df.iloc[-2]; price = curr['CLOSE']
    
    # 1. 动态基准分
    base_score = 3.0; regime_msg = ""
    if spy_trend == "Bull": base_score = 3.5; regime_msg = f"{FACTOR_COMMENTS['Regime_Bull']}"
    elif spy_trend == "Bear": base_score = 2.5; regime_msg = f"{FACTOR_COMMENTS['Regime_Bear']}"
    if vix_level > 25: base_score -= 0.5; regime_msg = f"{FACTOR_COMMENTS['Regime_Panic']} (VIX:{vix_level:.1f})"
    if vix_level > 35: base_score = 1.5
    base_score = max(1.5, base_score)

    # 2. 趋势
    try:
        df['HMA_55'] = df.ta.hma(length=55); df['HMA_144'] = df.ta.hma(length=144)
        hma55 = df['HMA_55'].iloc[-1]; hma144 = df['HMA_144'].iloc[-1]
    except: hma55=0; hma144=0
    
    trend_score = 1.0; trend_msg = ""
    if hma55 > hma144 and price > hma55: trend_score = 1.3; trend_msg = f"{FACTOR_COMMENTS['Trend_Bull']}"
    elif price < hma144: trend_score = 0.8; trend_msg = f"{FACTOR_COMMENTS['Trend_Bear']}"
    else: trend_score = 0.8; trend_msg = f"{FACTOR_COMMENTS['Trend_Chop']}"

    # 3. VSA
    vol_ma20 = df['VOLUME'].rolling(20).mean().iloc[-1]
    rvol = curr['VOLUME'] / vol_ma20 if vol_ma20 > 0 else 1.0
    price_change = (curr['CLOSE'] - prev['CLOSE']) / prev['CLOSE']
    up_vol = quote_data.get('upVolume') if quote_data else None
    down_vol = quote_data.get('downVolume') if quote_data else None
    
    vsa_score = 1.0; vsa_msg = ""
    if up_vol is not None and down_vol is not None:
        uv_ratio = up_vol / (up_vol + down_vol) if (up_vol+down_vol) > 0 else 0.5
        if rvol > 1.5:
            if uv_ratio < 0.35 and abs(price_change) < 0.02: vsa_score = 0.3; vsa_msg = f"{FACTOR_COMMENTS['VSA_Exit']}"
            elif abs(price_change) < 0.005: vsa_score = 0.5; vsa_msg = f"{FACTOR_COMMENTS['VSA_Churn']}"
            elif price_change > 0.03: vsa_score = 1.2; vsa_msg = f"{FACTOR_COMMENTS['VSA_Pump']}"
            elif price_change < -0.02: vsa_score = 0.5; vsa_msg = f"{FACTOR_COMMENTS['VSA_Dump']}"
        elif rvol < 0.7 and price > df['HIGH'].iloc[-21:-1].max(): vsa_score = 1.3; vsa_msg = f"{FACTOR_COMMENTS['VSA_Lock']}"
    else:
        range_len = curr['HIGH'] - curr['LOW']
        clv = (curr['CLOSE'] - curr['LOW']) / range_len if range_len > 0 else 0.5
        if rvol > 1.5 and price_change > 0.02 and clv > 0.7: vsa_score = 1.2; vsa_msg = f"{FACTOR_COMMENTS['VSA_Pump']} (K线)"
        elif rvol > 1.5 and clv < 0.3: vsa_score = 0.5; vsa_msg = f"{FACTOR_COMMENTS['VSA_Dump']} (K线)"
        elif rvol > 1.0 and price_change > 0 and clv > 0.8: vsa_score = 1.1; vsa_msg = f"{FACTOR_COMMENTS['VSA_Strong']}"

    # 4. 基本面
    fund_score = 1.0; fund_msg = ""
    if fundamentals:
        eps = fundamentals.get('eps', 0)
        rev_growth = fundamentals.get('rev_growth', 0)
        gross_margin = fundamentals.get('gross_margin', 0)
        fcf_yield = fundamentals.get('fcf_yield', 0)
        if eps < 0:
            if rev_growth < 0.15 and gross_margin < 0.30: fund_score = 0.0; fund_msg = f"{FACTOR_COMMENTS['Fund_Fake']}"
            else: fund_score = 0.9; fund_msg = f"{FACTOR_COMMENTS['Fund_Growth']}"
        elif rev_growth > 0.50 and gross_margin > 0.50: fund_score = 1.25; fund_msg = f"{FACTOR_COMMENTS['Fund_Super']}"
        elif fcf_yield > 0.05: fund_score = 1.3; fund_msg = f"{FACTOR_COMMENTS['Fund_Cash']}"
        else: fund_score = 1.1; fund_msg = f"{FACTOR_COMMENTS['Fund_Good']}"

    # 5. 板块 & 波动率
    sector_ret, etf_name = get_sector_momentum(ticker)
    sector_score = 1.0; sector_msg = ""
    if sector_ret > 0.05: sector_score = 1.2; sector_msg = f"{FACTOR_COMMENTS['Sector_Hot']} ({etf_name}: +{sector_ret*100:.1f}%)"
    elif sector_ret < -0.02: sector_score = 0.9; sector_msg = f"{FACTOR_COMMENTS['Sector_Cold']} ({etf_name}: {sector_ret*100:.1f}%)"

    atr = df.ta.atr(length=14).iloc[-1]
    atr_pct = atr / price if price > 0 else 0
    vol_score = 1.0; vol_msg = ""
    if atr_pct > 0.06: vol_score = 0.7; vol_msg = f"{FACTOR_COMMENTS['Vol_High']}"

    final_score = base_score * trend_score * vsa_score * fund_score * vol_score * sector_score
    
    special_signals = []
    try:
        df.ta.rsi(length=14, append=True)
        rsi = df['RSI_14'].iloc[-1]
        daily_range = curr['HIGH'] - curr['LOW']
        close_pos = (curr['CLOSE'] - curr['LOW']) / daily_range if daily_range > 0 else 0
        if rsi < 30 and price_change > 0.05 and rvol > 2.0 and close_pos > 0.7:
            final_score = 9.5; special_signals.append(f"🧊 **冰点反转确认**")
    except: pass

    try:
        ma144 = df['CLOSE'].rolling(144).mean().iloc[-1]; ma233 = df['CLOSE'].rolling(233).mean().iloc[-1]
        vol_ma50 = df['VOLUME'].rolling(50).mean().iloc[-1]; rvol_50 = curr['VOLUME'] / vol_ma50 if vol_ma50 > 0 else 1.0
        in_zone = (price < ma144 * 1.02 and price > ma233 * 0.98) or (abs(price - ma144)/price < 0.02)
        if in_zone and rvol_50 < 0.6 and ma144 > df['CLOSE'].rolling(144).mean().iloc[-10]:
            final_score = max(final_score, 9.9); special_signals.append(f"☢️ **机构建仓区启动**")
    except: pass
    
    try:
        highest_22 = df['HIGH'].rolling(22).max().iloc[-1]
        chandelier_stop = highest_22 - 3 * atr
        chandelier_stop = min(chandelier_stop, price * 0.98)
    except: chandelier_stop = price * 0.92
    
    debug_formula = f"{base_score}*{trend_score:.1f}*{vsa_score:.1f}*{fund_score:.1f}*{sector_score:.1f}"
    if vol_score != 1.0: debug_formula += f"*{vol_score:.1f}"
    
    return final_score, special_signals, chandelier_stop, atr_pct, trend_msg, vsa_msg, fund_msg, sector_msg, regime_msg, vol_msg, debug_formula

# 🔥 V33.8 核心修改：信念加仓法
def calculate_position_size(atr_pct, final_score):
    if final_score < 2.0: return "空仓/观望"
    
    # 动态风险预算 (Confidence Scaling)
    if final_score >= 9.0:
        risk_per_trade = 0.025 # 核武器/冰点：2.5% 风险
    elif final_score >= 7.0:
        risk_per_trade = 0.015 # 牛股：1.5% 风险
    else:
        risk_per_trade = 0.005 # 普通：0.5% 风险
        
    # 如果是核武器信号，止损收紧到 1.5 ATR 以换取更大仓位
    multiplier = 1.5 if final_score >= 9.0 else 3.0
    
    stop_distance_pct = multiplier * atr_pct
    if stop_distance_pct <= 0.001: return "0%"
    
    position_size = risk_per_trade / stop_distance_pct
    pos_pct = min(position_size * 100, 40) # 上限 40%
    
    return f"{int(pos_pct)}%"

# ================= Bot 指令 =================

@bot.tree.command(name="check", description="V33.8 终极信念版")
async def check_stocks(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    t = ticker.split()[0].replace(',', '').upper()
    
    loop = asyncio.get_running_loop()
    spy_trend, vix_level, _ = await loop.run_in_executor(None, get_market_regime_detailed)
    df, quote = await loop.run_in_executor(None, get_daily_data_stable, t)
    if df is None: return await interaction.followup.send(f"❌ 数据失败: {t}")
    fund = await loop.run_in_executor(None, get_fundamentals_deep, t)
    
    score, specials, chandelier, atr_pct, t_msg, v_msg, f_msg, s_msg, r_msg, vl_msg, formula = calculate_v33_score(df, quote, fund, spy_trend, vix_level, t)
    
    price = df['CLOSE'].iloc[-1]
    pos_advice = calculate_position_size(atr_pct, score)
    
    color = discord.Color.light_grey()
    if score > 5.0: color = discord.Color.green()
    if score > 8.0: color = discord.Color.gold()
    if score < 2.0: color = discord.Color.red()
    if any("冰点" in s for s in specials): color = discord.Color.blue()

    desc = f"**评分**: `{score:.2f}` | **环境**: `{r_msg.split('，')[0]}`\n"
    desc += f"**算式**: `{formula}`\n"
    desc += f"**仓位**: `{pos_advice}`\n"
    
    if "禁止" in t_msg: desc += f"**趋势警告**: 🚫 已跌破长期均线，禁止做多\n"
    desc += f"**多头止损**: `${chandelier:.2f}` (跌破即跑)\n"
    
    conc_title = "机构结论"; conc_val = "观望或回避"
    if score >= 9.5: conc_val = "战统行囊，死拿 (核武器/冰点)"
    elif score >= 7.0: conc_val = "主升浪进行中，可战略加仓"
    elif score >= 4.0: conc_val = "可建仓，控制节奏"
    elif score < 2.0: conc_val = "坚决回避/清仓"
    
    desc += "\n**因子扫描:**\n"
    if t_msg: desc += f"> {t_msg}\n"
    if s_msg: desc += f"> {s_msg}\n"
    if v_msg: desc += f"> {v_msg}\n"
    if f_msg: desc += f"> {f_msg}\n"
    if vl_msg: desc += f"> {vl_msg}\n"
    
    if specials:
        desc += "\n**绝密信号:**\n"
        for s in specials: desc += f"> {s}\n"

    ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
    embed = discord.Embed(title=f"{t} 机构专业版 (V33.8)", description=f"现价: ${price:.2f}\n{desc}", color=color)
    embed.set_image(url=get_finviz_chart_url(t))
    embed.add_field(name=conc_title, value=conc_val, inline=False)
    embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="list", description="扫描观察池")
async def list_stocks(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_stocks = watch_data.get(user_id, {})
    if not user_stocks: return await interaction.response.send_message("📭 列表为空")
    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()
    spy_trend, vix_level, _ = await loop.run_in_executor(None, get_market_regime_detailed)
    
    lines = []
    tickers = list(user_stocks.keys())
    for t in tickers:
        df, quote = await loop.run_in_executor(None, get_daily_data_stable, t)
        if df is None: continue
        fund = await loop.run_in_executor(None, get_fundamentals_deep, t)
        score, specials, _, _, _, _, _, _, _, _, _ = calculate_v33_score(df, quote, fund, spy_trend, vix_level, t)
        icon = "🔥" if score > 7 else "💀" if score < 2 else "⚖️"
        if any("冰点" in s for s in specials): icon = "🧊"
        lines.append(f"**{t}**: `{score:.1f}` {icon}")
    
    embed = discord.Embed(title="📊 V33.8 机构看板", description="\n".join(lines), color=discord.Color.blue())
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="add", description="添加")
async def add_stock(interaction: discord.Interaction, ticker: str):
    user_id = str(interaction.user.id)
    if user_id not in watch_data: watch_data[user_id] = {}
    for t in ticker.upper().replace(',', ' ').split(): watch_data[user_id][t] = {}
    save_data()
    await interaction.response.send_message(f"✅")

@bot.tree.command(name="remove", description="删除")
async def remove_stock(interaction: discord.Interaction, ticker: str):
    user_id = str(interaction.user.id)
    if user_id in watch_data and ticker.upper() in watch_data[user_id]:
        del watch_data[user_id][ticker.upper()]
        save_data()
        await interaction.response.send_message(f"🗑️")

@tasks.loop(time=datetime.time(hour=16, minute=15, tzinfo=pytz.timezone('America/New_York')))
async def daily_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    loop = asyncio.get_running_loop()
    spy_trend, vix_level, _ = await loop.run_in_executor(None, get_market_regime_detailed)
    api_cache_daily.clear(); api_cache_fund.clear(); api_cache_sector.clear()
    
    for uid, stocks in watch_data.items():
        summary_lines = []
        tickers = list(stocks.keys())
        for t in tickers:
            df, quote = await loop.run_in_executor(None, get_daily_data_stable, t)
            if df is None: continue
            fund = await loop.run_in_executor(None, get_fundamentals_deep, t)
            score, specials, stop, atr_pct, _, _, _, _, _, _, _ = calculate_v33_score(df, quote, fund, spy_trend, vix_level, t)
            
            if score >= 7.0 or score < 2.0 or specials:
                price = df['CLOSE'].iloc[-1]
                icon = "🔥" if score >= 7 else "💀"
                if any("冰点" in s for s in specials): icon = "🧊"
                spec_str = f" | {', '.join(specials)}" if specials else ""
                summary_lines.append(f"{icon} **{t}** ({score:.1f}): ${price:.2f}{spec_str}")

        if summary_lines:
            msg = f"📊 <@{uid}> **V33.8 核心简报** (VIX:{vix_level:.1f}):\n" + "\n".join(summary_lines)
            await channel.send(msg[:1900])
            await asyncio.sleep(1)

@tasks.loop(time=datetime.time(hour=9, minute=25, tzinfo=pytz.timezone('America/New_York')))
async def premarket_alert():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    loop = asyncio.get_running_loop()
    spy_trend, vix_level, _ = await loop.run_in_executor(None, get_market_regime_detailed)
    api_cache_daily.clear()
    for uid, stocks in watch_data.items():
        pre_alerts = []
        for t in list(stocks.keys()):
            df, quote = await loop.run_in_executor(None, get_daily_data_stable, t)
            if df is None: continue
            fund = await loop.run_in_executor(None, get_fundamentals_deep, t)
            score, specials, _, _, _, _, _, _, _, _, _ = calculate_v33_score(df, quote, fund, spy_trend, vix_level, t)
            if specials:
                price = df['CLOSE'].iloc[-1]
                pre_alerts.append(f"☢️ **{t}**: ${price:.2f} | {' '.join(specials)}")
        if pre_alerts:
            ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
            await channel.send(f"🌅 <@{uid}> **盘前绝密情报** ({ny_time}):\n" + "\n".join(pre_alerts))

@bot.event
async def on_ready():
    load_data()
    api_cache_daily.clear(); api_cache_fund.clear(); api_cache_sector.clear()
    logger.info("✅ V33.8 Ultimate Confidence Edition Started.")
    await bot.tree.sync()
    daily_monitor.start()
    premarket_alert.start()

bot.run(TOKEN)
