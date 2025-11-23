# --- 新增：周末/休市专用模拟指令 ---
@bot.command(name='sim')
async def simulate_alert(ctx, ticker: str = "TSLA"):
    """
    用法: /sim 或 /sim NVDA
    强制模拟一个报警信号，用于周末测试样式
    """
    ticker = ticker.upper()
    
    # 1. 伪造一些假数据
    fake_price = 123.45
    fake_change = 5.88 # 假装今天涨了 5.88%
    
    # 2. 生成真实的 Finviz 图表 (图表是真实的，显示的是周五收盘的状态)
    chart_url = get_finviz_chart_url(ticker)
    
    # 3. 构造 Embed (和真实报警一模一样)
    embed = discord.Embed(
        title=f"🚀 [模拟测试] 异动警报: {ticker} 暴力拉升",
        description=f"**当前涨幅**: +{fake_change}%\n**现价**: ${fake_price}\n\n⚠️ 注意：这是手动触发的测试消息，非实时行情。",
        color=discord.Color.green()
    )
    embed.set_image(url=chart_url)
    embed.set_footer(text="监控阈值: ±TEST% • 模拟触发")
    embed.timestamp = datetime.datetime.now()
    
    await ctx.send(embed=embed)
