import hashlib
import hmac
import json
import logging
import os
import sys

from datetime import datetime
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from ta.momentum import RSIIndicator
from dotenv import load_dotenv

API_key = os.getenv("API_KEY")
API_secret = os.getenv("API_SECRET")

# Init log paths
LOGS_PATH = [f'./logs/transactions', f'./logs/runtime', f'./logs/tracker']
for path in LOGS_PATH:
    os.makedirs(path, exist_ok=True)

# init config file
if not os.path.isfile('./config/spot_config.csv'):
    res = requests.get('https://nami.exchange/api/v3/spot/config')
    pd.DataFrame(res.json()['data']).to_csv('./config/spot_config.csv', index=False)



TRANSACTION_PATH, RUNTIME_PATH, TRACKER_PATH, *_ = LOGS_PATH

# Get today datetime info
this_hour = datetime.now().strftime('%H:%M:%S')
today = datetime.now().date()
this_month = datetime.now().strftime("%m-%Y")

# Set the logging level for urllib3 to WARNING
urllib3_logger = logging.getLogger('urllib3')
urllib3_logger.setLevel(logging.WARNING)

logging.basicConfig(filename=f'{RUNTIME_PATH}/{today}.log',
                    filemode='a',
                    format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.DEBUG)

tf = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1h': 3600,
    '4h': 14400,
    '1d': 86400,
}


class APIUrl:
    get_user_balance = ['get', 'https://nami.exchange/api/v4/user/balance']
    create_order = ['post', 'https://nami.exchange/api/v4/spot/order']
    close_order = ['delete', 'https://nami.exchange/api/v4/spot/order']
    get_order_history = ['get', 'https://nami.exchange/api/v4/spot/history']
    get_open_order = ['get', 'https://nami.exchange/api/v4/spot/open']
    get_market_depth = ['get', 'https://nami.exchange/api/v3/spot/depth']


def get_signature(params: dict, secret: str):
    query_items = []
    for key, value in params.items():
        if value is not None:
            if isinstance(value, float):
                if value.is_integer():
                    q = f"{key}={int(value)}"
                else:
                    q = f"{key}={value:.9f}".rstrip('0')  # round to 9 and remove left over 0
            else:
                q = f"{key}={json.dumps(value)}"
            query_items.append(q)

    query_string = "&".join(query_items)

    signature = hmac.new(
        bytes(secret, 'latin-1'),
        msg=bytes(query_string, 'latin-1'),
        digestmod=hashlib.sha256
    ).hexdigest()
    return signature, f'{query_string}&signature={signature}'


def request_nami_api(params: dict = None, api_attr: list = None, body=None):
    logging.info(f"=== request_nami_api === \n{params} {api_attr}")
    if params is None:
        params = {
            'timestamp': int(datetime.now().timestamp()) * 1000
        }
    if api_attr is None:
        api_attr = APIUrl.get_open_order
    if body is None:
        body = {}

    query_params: dict = params.copy()
    query_params.update(body)

    signature, signed = get_signature(query_params, API_secret)

    query_params['signature'] = signature

    method, url = api_attr
    headers = {
        'x-api-key': API_key,
        'Content-Type': "application/json"
    }
    response = requests.request(method=method.upper(), url=f'{url}?{signed}', headers=headers,
                                json=query_params if method.lower() != 'get' else None)
    data = response.json()
    logging.info(f'URL: {url}?{signed}')
    logging.info(data)
    #
    if data.get('message').lower() != 'ok' or data.get('data').get('data').get('requestId'):
        return None
    return data


def data_analysis(symbols=None, resolution: str = '1h', n_rows: int = 100):
    """ fast analysis of data
    PARAMS:
    - symbols: list of symbols
    - resolution: 1m, 1h, 1d, 5m, 15m
    """
    if symbols is None:
        symbols = []
    wrap_time = 0
    if resolution not in ['1m', '1h', '1d']:
        if resolution == '5m':
            wrap_time = 300
            resolution = '1m'
            n_rows = n_rows * 5
        elif resolution == '15m':
            wrap_time = 900
            resolution = '1m'
            n_rows = n_rows * 15
        else:
            raise ValueError('Resolution not supported')

    base_url = 'https://datav2.nami.exchange/api/v1/chart/history'
    param = {
        'from': int(datetime.now().timestamp()) - tf[resolution] * n_rows,
        'to': int(datetime.now().timestamp()) + 5,
        'resolution': resolution,
        'broker': 'NAMI_SPOT',
        'symbol': ''
    }
    result = []
    for symbol in symbols:
        param['symbol'] = symbol
        query = urlencode(param)
        url = f"{base_url}?{query}"
        response = requests.get(url)
        if response.status_code != 200:
            logging.info(f"Error: {symbol}")
            continue
        data = pd.DataFrame(response.json(),
                            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'trades'])
        if wrap_time > 0:
            data['close_time'] = ((data['timestamp'] + wrap_time - 5) // wrap_time) * wrap_time
            data = data.groupby('close_time').agg(
                {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
                 'trades': 'sum'}).reset_index(names="timestamp")

        data['rsi'] = RSIIndicator(data['close'], 7).rsi()
        rsi = data['rsi'].values[-10:]
        ep_num = bool(rsi[-2] <= 30 and rsi[-2] < rsi[-1] and rsi[-2] < rsi[-3])
        # ep_num = int(sum([v < 30 and v < rsi[i - 1] and v < rsi[i + 1] 
        if ep_num > 0:
            len_rsi = len(rsi)
            for i in range(len_rsi-3, -1, -1):
                if rsi[i] > 40:
                    break
                elif rsi[i] <= 30 and rsi[i] < rsi[i - 1] and rsi[i] < rsi[i + 1]:
                    ep_num += 1

        result.append((symbol, ep_num, data[-15:].copy()))
    return result


def data_analysis_v2(symbols=None, timeframe: str = '1h', n_rows: int = 50):
    """ light version of data_analysis having more indicators, analysis but having latency up to 2 minutes
    PARAMS:
    - symbols: list of symbols
    - timeframe: 5m, 10m, 15m, 30m, 1h, 4h, 1d
    - n_rows: number of rows to get
    """
    url = 'https://api.vistia.co/api/v2_1/prices/chart/indicators'
    now = int(datetime.now().timestamp())
    from_time = now - tf[timeframe] * (n_rows + 1)
    to_time = now + 5
    result = {}
    for symbol in symbols:
        response = requests.get(f"{url}?symbol={symbol}&from_time={from_time}&to_time={to_time}&timeframe={timeframe}")
        data = response.json()
        df = pd.DataFrame.from_records(data)
        result.update({symbol: df})
    return result


def get_price(symbol=None, quantity=None, side='BUY'):
    # quantity is the quantity of coin want to buy/sell
    try:
        params = {'symbol': symbol}

        data = request_nami_api(params, api_attr=APIUrl.get_market_depth)['data']
        data = data['asks' if side == 'BUY' else 'bids']
        i = 0
        value = 0
        q = quantity
        while q > 0:
            value += data[i][0] * min(q, data[i][1])
            q -= data[i][1]
            i += 1
        return value / quantity
        # todo: if quantity too big market will not enough to buy / sell
    except Exception as e:
        logging.info('---------------------')
        logging.info(e)
        return None


def log_func(data: dict):
    # Create new directory
    dir_path = f'{TRANSACTION_PATH}/{this_month}'
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

    filepath = f'{dir_path}/{today}.csv'

    # Tweaking feeMetadata in data
    fee_metadata = data.get('feeMetadata')
    if isinstance(fee_metadata, dict):
        for key in fee_metadata:
            data[f'feeMetadata - {key}'] = fee_metadata.get(key)

    data['hour'] = f'{this_hour}'
    columns = ['hour', 'symbol', 'displayingId', 'price', 'stopPrice', 'quantity', 'quoteQty', 'side', 'type', 'ep_num',
               'feeMetadata - asset', 'feeMetadata - value', 'feeMetadata - feeRatio', 'feeMetadata - executed']
    if os.path.exists(filepath):  # append new record if file already exist
        new_row = pd.DataFrame(data=data, columns=columns, index=[0])
        new_row.to_csv(filepath, mode='a', index=False, header=False)
    else:  # create new log file for each day
        csv_data = pd.DataFrame(data=data, columns=columns, index=[0])
        csv_data.to_csv(filepath, index=False)


class NamiTradeBot:
    def __init__(self, call_budget: float = 5.0, symbols=None):
        # Configuration
        self.call_budget = call_budget
        self.symbols = symbols or ['BTCVNST', 'ETHVNST', 'USDTVNST']

        # load symbol info
        df = pd.read_csv(f'./config/spot_config.csv', index_col='symbol')
        self.symbol_info = df.loc[self.symbols].to_dict(orient='index')
        self.rsi_call_setting = {
            "call_multiplier": {
                1: 1,
                2: 1.5,
                (3, 4, 5): 2,
            },
            "sell_limit": {
                1: 1.01,
                2: 1.015,
                (3, 4, 5): 1.015,
            }
        }

    def analyzer(self, resolution='1h', n_rows=100):
        datas = data_analysis(self.symbols, resolution, n_rows=n_rows)

        # Create BUY orders
        for d in datas:
            symbol, ep_num, others_d = d
            logging.info(f'Symbol: {symbol}, RSI:\n{others_d["rsi"][-10:]}')
            if ep_num:
                token_step_size = self.symbol_info.get(symbol).get('LOT_SIZE_stepSize', 1)
                price_tick_size = self.symbol_info.get(symbol).get('PRICE_FILTER_tickSize', 1)

                # get decimals
                token_decimal = int(-np.log10(token_step_size))
                price_decimal = int(-np.log10(price_tick_size))

                p = others_d.tail(1)['close'].iloc[0]
                p = round(p, price_decimal)

                # call value after multiplier
                call_value = self.rsi_call_setting.get("call_multiplier").get(ep_num, 1) * self.call_budget
                sell_price = self.rsi_call_setting.get("sell_limit").get(ep_num, 1) * p  # expected sell price
                qty = round(call_value / p, token_decimal)

                results = self.create_order(symbol=symbol, price=p, quantity=qty, side='BUY', type_='MARKET',
                                            ep_num=ep_num)
                if results:
                    bought_qty = results.get('data').get('data').get('quantity')
                    sell_p = round(sell_price, price_decimal)
                    self.create_order(symbol=symbol, price=sell_p, quantity=bought_qty, side='SELL', type_='LIMIT',
                                      ep_num=ep_num)
                else:
                    logging.info('no qty')
                    print("no qty")

        # # Updating sell_orders - comment for later use
        # for order_id in self.sell_orders:
        #     symbol, quantity = order_id.get('symbol'), order_id.get('quantity')
        #     self.update_sell_orders(symbol=symbol, quantity=quantity, displaying_id=order_id)

    def analyzer_v2(self, resolution='1h'):
        datas = data_analysis_v2(self.symbols, resolution, n_rows=100)
        for symbol, data in datas.items():
            # ep_num = None
            # if data.tail(2)[['rsi7_epl','rsi14_epl']].iloc[0].sum() > 0:
            #     ep_num = int(data.tail(10)['rsi7_epl'].sum())
            if data.tail(2)[['rsi7_epl', 'rsi14_epl']].iloc[0].sum() > 0:
                token_step_size = self.symbol_info.get(symbol).get('LOT_SIZE_stepSize', 1)
                price_tic_size = self.symbol_info.get(symbol).get('PRICE_FILTER_tickSize', 1)
                p = data.tail(1)['close'].iloc[0]
                p = float(np.ceil(p / price_tic_size) * price_tic_size)

                qty = float(np.ceil(self.call_budget / token_step_size / p) * token_step_size)
                self.create_order(symbol=symbol, price=p, quantity=qty, side='BUY', type_='MARKET')

    def create_order(self, symbol, price, quantity, side: str, type_: str, stop_p=None, ep_num=-1):
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": type_.upper(),
            "quantity": quantity,
            "quoteOrderQty": round(price * quantity, 2),
            "price": price,
            "useQuoteQty": 'false',
            "timestamp": int(datetime.now().timestamp()) * 1000
        }

        if stop_p:
            params['stopPrice'] = stop_p

        data = request_nami_api(params, APIUrl.create_order)

        if data and not data.get('data').get('data').get('requestId'):
            content = data.get('data').get('data')
            content['ep_num'] = ep_num
            if data.get('message').lower() == 'ok':
                log_func(data=content)
        else:
            logging.info('Request fail')
            if data:
                logging.info(data)
            else:
                logging.info('No data returned')
        return data

    # self.sell_order removed
    def update_sell_orders(self, symbol: str, quantity: float, displaying_id, price=None, stop_price=None,
                           limit_price=None, stop_rate=0.99, stop_limit_rate=0.995):
        if price is None:
            price = get_price(symbol, quantity, 'SELL')
        if stop_price is None:
            stop_price = price * stop_rate
        if limit_price is None:
            limit_price = price * stop_limit_rate

        need_place_order = True
        for i, order in enumerate(self.sell_orders):
            if order.get('displayingId') == displaying_id:
                stop_price = order.get('stopPrice')
                symbol = order.get('symbol')

                if order['stopPrice'] < stop_price:
                    logging.info(f"stop_limit {displaying_id} status: {order.get('status')}")
                    close_body = {
                        'symbol': symbol,
                        'displayingId': displaying_id,
                        'allowNotification': 'false',
                        'timestamp': int(datetime.now().timestamp()) * 1000
                    }
                    order = request_nami_api(api_attr=APIUrl.close_order, body=close_body)
                    if order is None:  # fail to close order
                        logging.info(f"Failed to close order {displaying_id}")
                        return None
                    else:
                        self.sell_orders[i] = order.get('data')
                else:
                    need_place_order = False
                break

        if need_place_order:
            self.create_order(symbol=symbol, price=limit_price, quantity=quantity, side='SELL', type_="STOP_LIMIT",
                              stop_p=stop_price)


def test():
    print('Run test')
    test_bot = NamiTradeBot(call_budget=10, symbols=['BTCVNST', 'ETHVNST', 'USDTVNST'])
    a = test_bot.create_order(symbol='BTCVNST', price=102000, quantity=0.0001, side='SELL', type_='LIMIT')
    print(a)


