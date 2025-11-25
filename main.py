# ================= 📈 V28.1 核心分析逻辑 (修复 KeyError 版) =================
def analyze_daily_signals(ticker):
    df = get_daily_data_stable(ticker)
    if df is None or len(df) < 100: return None, None, None # 注意这里返回3个None
    
    # 🚨🚨🚨 【核心修复】 🚨🚨🚨
    # 强制将所有列名转换为大写 (解决 KeyError: 'CLOSE')
    df.columns = [str(c).upper() for c in df.columns]
    
    signals = []
    
    # 1. 指标计算 (只算高信噪比的)
    # pandas_ta 会自动识别大写的 OPEN/HIGH/LOW/CLOSE
    df.ta.supertrend(length=10, multiplier=3, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.aroon(length=25, append=True)
    df.ta.cmf(length=20, append=True)
    df['VOL_MA_20'] = df.ta.sma(close='volume', length=20) # 这里的 'volume' pandas_ta会自动匹配大写的 VOLUME
    df.ta.kc(length=20, scalar=2, append=True) # 肯特纳通道
    df.ta.rsi(length=14, append=True)
    
    # 手动计算一目均衡 (最核心风控)
    # 此时 df['HIGH'] 等已经是大写，引用正确
    high9 = df['HIGH'].rolling(9).max(); low9 = df['LOW'].rolling(9).min()
    df['tenkan'] = (high9 + low9) / 2
    high26 = df['HIGH'].rolling(26).max(); low26 = df['LOW'].rolling(26).min()
    df['kijun'] = (high26 + low26) / 2
    high52 = df['HIGH'].rolling(52).max(); low52 = df['LOW'].rolling(52).min()
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
