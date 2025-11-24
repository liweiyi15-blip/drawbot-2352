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

# ================= 🧠 战法说明书 =================
def get_signal_advice(t):
    advice = ""
    # 0. 💎 估值/事件
    if "财报" in t: advice = "高危事件: 官方日历显示临近财报（T-5），波动率极高，建议回避。"
    elif "DCF 低估" in t: advice = "价值洼地: 价格低于未来现金流折现，具备长期安全边际。"
    elif "DCF 高估" in t: advice = "价值透支: 价格远超内在价值，需警惕回归风险。"
    elif "PEG 低估" in t: advice = "成长价值: 业绩正增长且估值合理，优质GARP标的。"
    elif "PEG 高估" in t: advice = "增长陷阱: 业绩增速撑不起当前的股价。"
    
    # 1. ⏳ 择时
    elif "九转" in t and "买入" in t: advice = "九转底部: 连跌9天，极度超跌，反弹一触即发。"
    elif "九转" in t and "卖出" in t: advice = "九转顶部: 连涨9天，情绪过热，建议分批止盈。"
    elif "十三转" in t and "底部" in t: advice = "终极底部: 趋势衰竭的极值，左侧交易皇冠上的明珠。"
    elif "十三转" in t and "顶部" in t: advice = "终极顶部: 趋势高潮的极值，风险极大，必须清仓。"
    # 2. 💰 资金
    elif "盘中爆量" in t: advice = "主力异动: 日内大资金突袭，方向可信度极高。"
    elif "放量" in t: advice = "量价配合: 上涨有真金白银支持，趋势健康。"
    elif "缩量" in t: advice = "洗盘迹象: 交易清淡，卖盘枯竭，关注变盘。"
    # 3. 🕯️ 形态
    elif "早晨" in t: advice = "黎明曙光: 经典底部反转形态，多头吹响号角。"
    elif "黄昏" in t: advice = "夜幕降临: 经典顶部反转形态，空头开始反击。"
    elif "吞没" in t or "阳包阴" in t or "阴包阳" in t: advice = "反包形态: 一举吃掉昨日K线，力量对比发生逆转。"
    elif "锤子" in t: advice = "金针探底: 下方支撑强劲，多头抵抗激烈。"
    elif "断头" in t: advice = "一刀切: 大阴线切断多条均线，趋势破坏。"
    # 4. 📈 趋势
    elif "多头排列" in t: advice = "最强趋势: 均线全线发散向上，持股待涨。"
    elif "空头排列" in t: advice = "最弱趋势: 均线全线发散向下，空仓观望。"
    elif "年线" in t: advice = "牛熊分界: 200日均线是机构生命线，长线分水岭。"
    elif "唐奇安" in t: advice = "海龟法则: 突破20日极值，顺势操作不猜顶底。"
    elif "Nx 突破" in t: advice = "Nx买点: 突破双通道压力，开启加速行情。"
    elif "Nx 站稳" in t: advice = "Nx持股: 价格运行在蓝梯之上，趋势完好。"
    elif "Nx 跌破" in t: advice = "Nx离场: 跌破短期生命线，注意风险。"
    elif "R1" in t or "S1" in t: advice = "关键位: 斐波那契重要阻力/支撑测试。"
    # 5. 🌊 摆动
    elif "背离" in t: advice = "先行指标: 价格与指标背道而驰，原有趋势即将终结。"
    elif "金叉" in t: advice = "动能增强: 买方力量占据上风。"
    elif "死叉" in t: advice = "动能减弱: 卖方力量占据上风。"
    elif "布林" in t: advice = "变盘节点: 突破波动率轨道，行情加速。"
    elif "超买" in t: advice = "情绪过热: 获利盘随时可能兑现。"
    elif "超卖" in t: advice = "情绪冰点: 恐慌盘杀出，关注反弹。"
    return advice

# ================= ⚖️ 评分系统 =================
def get_signal_category_and_score(s):
    s = s.strip()
    # 0. 💎 基本面/估值/事件
    if "财报" in s: return 'fundamental', 0 
    if "DCF" in s:
        if "低估" in s: return 'fundamental', 3
        if "高估" in s: return 'fundamental', -3
    if "PEG" in s:
        if "低估" in s: return 'fundamental', 2
        if "高估" in s: return 'fundamental', -2

    # 1. ⏳ 择时
    if "九转" in s or "十三转" in s:
        if "买入" in s or "底部" in s: return 'timing', 4
        if "卖出" in s or "顶部" in s: return 'timing', -4
    # 2. 💰 资金
    if "盘中爆量" in s: return 'volume', 4 if "抢筹" in s else -4
    if "放量" in s: return 'volume', 3 if "大涨" in s else -3
    if "缩量" in s: return 'volume', 1 if "回调" in s else -1
    # 3. 🕯️ 形态
    p_bull = ["早晨之星", "阳包阴", "锤子"]
    p_bear = ["断头铡刀", "阴包阳", "射击之星", "黄昏之星", "墓碑"]
    if any(x in s for x in p_bull): return 'pattern', 4
    if any(x in s for x in p_bear): return 'pattern', -4
    # 4. 📈 趋势
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
    # 5. 🌊 摆动
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
    active_blocks = [] 
    inactive_lines = []
    
    for item in items:
        score_val = item['score']
        score_str = f"+{score_val}" if score_val > 0 else f"{score_val}"
        
        if item['active']:
            total_score += score_val
            block = f"### {item['raw']} ({score_str})"
            advice = get_signal_advice(item['raw'])
            if advice: block += f"\n> {advice}"
            active_blocks.append(block)
        else:
            if score_val != 0:
                inactive_lines.append(f"> 🔸 {item['raw']} ({score_str}) [已去重]")

    final_text = "\n".join(active_blocks)
    if inactive_lines: final_text += "\n\n" + "\n".join(inactive_lines)
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
    score_title = f"+{score}" if score > 0 else f"{score}"
    return f"{status} ({score_title}) {icons}", color

# ================= FMP API =================
def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

# V7.4: 采用官方日历接口 (Official Calendar Endpoint)
def get_valuation_and_earnings(ticker, current_price):
    if not FMP_API_KEY: return []
    sigs = []
    try:
        # 1. 📅 官方财报日历 (Historical/Upcoming)
        # 文档: https://site.financialmodelingprep.com/developer/docs/earnings-calendar-api
        # 逻辑: 取列表，解析日期，找最近的未来日期
        cal_url = f"https://financialmodelingprep.com/api/v3/historical/earning_calendar/{ticker}?limit=10&apikey={FMP_API_KEY}"
        cal_resp = requests.get(cal_url, timeout=5)
        
        if cal_resp.status_code == 200:
            cal_data = cal_resp.json()
            if cal_data:
                today = datetime.date.today()
                # 遍历列表寻找未来日期
                # 列表通常是降序排列，但为了保险，我们遍历查找 >= today 的最小日期
                future_dates = []
                for entry in cal_data:
                    date_str = entry.get('date')
                    if date_str:
                        try:
                            e_date = parser.parse(date_str).date()
                            if e_date >= today:
                                future_dates.append(e_date)
                        except: pass
                
                if future_dates:
                    # 找到离今天最近的未来日期
                    next_date = min(future_dates)
                    days_diff = (next_date - today).days
                    if 0 <= days_diff <= 5:
                        sigs.append(f"⚠️ 财报预警 (T-{days_diff}天)")

        # 2. PEG & DCF 基础数据
        # 获取 EPS 以清洗 PEG
        quote_url = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={FMP_API_KEY}"
        quote_resp = requests.get(quote_url, timeout=5)
        eps = 0
        if quote_resp.status_code == 200:
            q_data = quote_resp.json()
            if q_data: eps = q_data[0].get('eps', 0)

        # 获取 DCF
        dcf_url = f"https://financialmodelingprep.com/api/v3/discounted-cash-flow/{ticker}?apikey={FMP_API_KEY}"
        dcf_resp = requests.get(dcf_url, timeout=5)
        if dcf_resp.status_code == 200:
            d_data = dcf_resp.json()
            if d_data and 'dcf' in d_data[0]:
                dcf = d_data[0]['dcf']
                if dcf > 0:
                    if current_price < dcf * 0.8: sigs.append(f"💎 DCF 低估 (估值:${dcf:.1f})")
                    elif current_price > dcf * 1.5: sigs.append(f"💎 DCF 高估 (估值:${dcf:.1f})")

        # 获取 PEG
        ratios_url = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{ticker}?apikey={FMP_API_KEY}"
        ratios_resp = requests.get(ratios_url, timeout=5)
        if ratios_resp.status_code == 200:
            r_data = ratios_resp.json()
            if r_data and 'pegRatioTTM' in r_data[0]:
                peg = r_data[0]['pegRatioTTM']
                # 硬过滤：EPS必须为正，PEG才有效
                if peg is not None and eps > 0:
                    if 0 < peg < 1.0: sigs.append(f"💎 PEG 低估 ({peg:.2f})")
                    elif peg > 2.0: sigs.append(f"💎 PEG 高估 ({peg:.2f})")

    except Exception as e:
        print(f"Valuation Error {ticker}: {e}")
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
    except Exception as e:
        print(f"❌ 数据处理异常 {ticker}: {e}")
        return None

def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 250: return None, None
    signals = []
    
    # 指标计算
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
    df.ta.willr(length=14, append=True); df.ta.cci(length=20, append=True)
    df.ta.adx(length=14, append=True); df.ta.obv(append=True)
    df.ta.atr(length=14, append=True); df.ta.donchian(lower_length=20, upper_length=20, append=True)
    try: df.ta.pivots(type="fibonacci", append=True)
    except: pass
    
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20)
    df.columns = [str(c).upper() for c in df.columns]

    curr = df.iloc[-1]; prev = df.iloc[-2]; 
    price = curr['CLOSE']

    # --- 0. 估值/财报分析 (V7.4 修正版) ---
    val_sigs = get_valuation_and_earnings(ticker, price)
    signals.extend(val_sigs)

    # --- 1. 内置算法: 九转/十三转 ---
    try:
        work_df = df.iloc[-50:].copy()
        c = work_df['CLOSE'].values
        h = work_df['HIGH'].values
        l = work_df['LOW'].values
        
        buy_setup = 0; sell_setup = 0
        for i in range(4, len(c)):
            if c[i] > c[i-4]: sell_setup += 1; buy_setup = 0
            elif c[i] < c[i-4]: buy_setup += 1; sell_setup = 0
            else: buy_setup = 0; sell_setup = 0
        
        if buy_setup == 9: signals.append("神奇九转: 底部买入信号 (9)")
        elif sell_setup == 9: signals.append("神奇九转: 顶部卖出信号 (9)")
        if buy_setup == 13: signals.append("迪玛克十三转: 终极底部 (13)")
        elif sell_setup == 13: signals.append("迪玛克十三转: 终极顶部 (13)")
    except Exception as e: print(f"Algo Error: {e}")

    # Nx
    is_break_blue = prev['CLOSE'] < prev['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_BLUE_UP']
    if curr['CLOSE'] > curr['NX_BLUE_UP'] and curr['CLOSE'] > curr['NX_YELL_UP']:
        if is_break_blue: signals.append("Nx 突破双梯")
        elif curr['CLOSE'] > curr['NX_BLUE_DW']: signals.append("Nx 站稳蓝梯")
    if curr['NX_BLUE_DW'] > curr['NX_YELL_UP']: signals.append("Nx 牛市排列")
    elif curr['NX_YELL_DW'] > curr['NX_BLUE_UP']: signals.append("Nx 熊市压制")

    # 量能
    vol_ma = curr['VOL_MA_20']
    if pd.notna(vol_ma) and vol_ma > 0:
        rvol = curr['VOLUME'] / vol_ma
        if rvol > 2.0 and curr['CLOSE'] > prev['CLOSE']: signals.append(f"放量大涨 (量比:{rvol:.1f}x)")
        elif rvol > 2.0 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"放量大跌 (量比:{rvol:.1f}x)")
        elif rvol < 0.6 and curr['CLOSE'] < prev['CLOSE']: signals.append(f"缩量回调 (量比:{rvol:.1f}x)")

    # 支撑压力
    if 'P_FIB_R1' in df.columns and prev['CLOSE'] < curr['P_FIB_R1'] and curr['CLOSE'] > curr['P_FIB_R1']: signals.append(f"突破 R1 阻力")
    if curr['CLOSE'] > prev['DCU_20_20']: signals.append(f"突破唐奇安上轨")
    
    # 均线
    if (curr['SMA_5'] > curr['SMA_10'] > curr['SMA_20'] > curr['SMA_60']): signals.append("均线多头排列")
    if (curr['SMA_5'] < curr['SMA_10'] < curr['SMA_20'] < curr['SMA_60']): signals.append("均线空头排列")
    if prev['CLOSE'] < prev['SMA_200'] and curr['CLOSE'] > curr['SMA_200']: signals.append("🐂 突破年线 MA200")
    if prev['CLOSE'] > prev['SMA_200'] and curr['CLOSE'] < curr['SMA_200']: signals.append("🐻 跌破年线 MA200")

    # 震荡
    if curr['RSI_14'] > 75: signals.append(f"RSI 超买 ({curr['RSI_14']:.1f})")
    elif curr['RSI_14'] < 30: signals.append(f"RSI 超卖 ({curr['RSI_14']:.1f})")
    
    # K线
    body = abs(curr['CLOSE'] - curr['OPEN'])
    lower_shadow = min(curr['CLOSE'], curr['OPEN']) - curr['LOW']
    if body > 0 and lower_shadow > (body * 2) and curr['RSI_14'] < 50: signals.append("锤子线")
    if prev['CLOSE'] < prev['OPEN'] and curr['CLOSE'] > curr['OPEN'] and curr['OPEN'] < prev['CLOSE'] and curr['CLOSE'] > prev['OPEN']: signals.append("阳包阴")

    return price, signals

# ================= Bot 指令集 =================
@bot.event
async def on_ready():
    load_data()
    print(f'✅ V7.4 机构合规版Bot已启动: {bot.user}')
    await bot.tree.sync()
    if not daily_monitor.is_running(): daily_monitor.start()

@bot.tree.command(name="help_bot", description="显示指令手册")
async def help_bot(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 指令手册 (V7.4)", color=discord.Color.blue())
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
        
        score, desc_final = generate_report_content(signals)
        text_part, color = format_dashboard_title(score)
        
        embed = discord.Embed(title=f"{ticker} : {text_part}", description=f"**现价**: ${price:.2f}\n\n{desc_final}", color=color)
        embed.set_image(url=get_finviz_chart_url(ticker))
        
        ny_time = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%H:%M')
        embed.set_footer(text=f"FMP Ultimate API • 机构级多因子模型 • 今天 {ny_time}")
        
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
