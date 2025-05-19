import sys
from agent_bot.cex_trade_bot import NamiTradeBot
from agent_bot.dex_trade_bot import BscTradeBot
from datetime import datetime
import logging


if __name__ == '__main__':
    platform = str(sys.argv[1]).lower()
    if platform == 'dex':
        bot = NamiTradeBot(call_budget=10, symbols=['BTCVNST', 'ETHVNST', 'USDTVNST'])
    else:
        bot = BscTradeBot(call_budget=10, symbols=['BTCVNST', 'ETHVNST', 'USDTVNST'])

    try:
        start_time = datetime.now()
        logging.info(f'Start running at: {start_time}')

        # Initialize trade bot
        bot = NamiTradeBot(call_budget=10, symbols=['BTCVNST', 'ETHVNST', 'USDTVNST'])
        bot.analyzer(resolution='5m')
        # bot.test()
    except Exception as e:
        logging.error(f'\n{e}')

    logging.info('-------- end of run --------')
