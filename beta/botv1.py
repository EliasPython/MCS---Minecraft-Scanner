from javascript import require, On, Once, AsyncTask, once, off

# Import the javascript libraries
mineflayer = require("mineflayer")

# Create bot with basic parameters
bot = mineflayer.createBot(
    {"username": "mcs-bot", "host": "91.107.229.15", "port": 25565, "version": "1.21.11", "hideErrors": False}
    
)

# Login event required for bot
@On(bot, "login")
def login(this):
    pass