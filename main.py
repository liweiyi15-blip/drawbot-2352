import discord
from discord.ext import commands, tasks
import random
import datetime
import os # 引入os库读取环境变量

# --- 从环境变量读取配置 (安全做法) ---
TOKEN = os.getenv('DISCORD_TOKEN') 
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0')) # 默认为0，防止报错

HOT_STOCKS = ['TSLA', 'NVDA', 'AAPL', 'AMD', 'MSFT', 'COIN', 'MSTR', 'AMZN', 'GOOGL', 'META']

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

def get_finviz_chart_url(ticker):
    timestamp = int(datetime.datetime.now().timestamp())
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l&_{timestamp}"

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    if CHANNEL_ID != 0 and not auto_post_analysis.is_running():
        auto_post_analysis.start()
    else:
        print("警告: 未设置 CHANNEL_ID，自动推送未启动。")

@bot.command(name='ta')
async def technical_analysis(ctx, ticker: str):
    ticker = ticker.upper()
    chart_url = get_finviz_chart_url(ticker)
    embed = discord.Embed(title=f"📈 {ticker} 技术分析", color=discord.Color.gold())
    embed.set_image(url=chart_url)
    embed.set_footer(text="来源: Finviz")
    await ctx.send(embed=embed)

@tasks.loop(hours=4)
async def auto_post_analysis():
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        ticker = random.choice(HOT_STOCKS)
        embed = discord.Embed(title=f"🔥 热门异动推送: {ticker}", color=discord.Color.red())
        embed.set_image(url=get_finviz_chart_url(ticker))
        embed.timestamp = datetime.datetime.now()
        await channel.send(embed=embed)

bot.run(TOKEN)
