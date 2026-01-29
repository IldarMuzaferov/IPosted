import os
from aiogram import Bot, Dispatcher
from dotenv import find_dotenv, load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import logging


logging.basicConfig(level=logging.INFO)

load_dotenv(find_dotenv())


bot = Bot(token=os.getenv('TOKEN'), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
bot.my_admins_list = []

dp = Dispatcher()
