import discord
from dotenv import load_dotenv
from discord.ext import commands
import asyncio
import datetime
import logging
import os

log_dir = 'log'
os.makedirs(log_dir, exist_ok=True)
log_filename = os.path.join(log_dir, datetime.datetime.now().strftime('%Y-%m-%d.log'))
logging.basicConfig(filename=log_filename, 
                    level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

load_dotenv()   
TOKEN = os.getenv("TOKEN")
application_id = os.getenv("application_id")
bot = commands.Bot(command_prefix=".",intents=discord.Intents.all(),application_id=application_id)

@bot.event
async def on_ready():
    print("更新指令")
    await bot.tree.sync()
    print("更新指令完成")
    print("更新簡介")
    text = "shiro_AC v0.1.0"
    print(text)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))
    print("更新簡介完成")
    print("啟動成功")

@bot.event
async def on_interaction(i: discord.Interaction):
    if i.type == discord.InteractionType.application_command:
        user = i.user
        command_name = i.data['name']
        channel = i.channel
        log_message = f"{user} in {channel} use: {command_name}"
        logging.info(log_message)
        print(log_message)

async def load():
    for file in os.listdir('./cmds'):
        if file.endswith('.py'):
            await bot.load_extension(f'cmds.{file[:-3]}')

async def main():
    await load()
    await bot.start(TOKEN)

asyncio.run(main())
