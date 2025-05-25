import os
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine
import pandas as pd 

# Load environment variables
load_dotenv()
TABLE_NAME  = os.getenv("TABLE_NAME", "coin_prices")
symbol_list  = os.getenv("SYMBOLS", "USDTVNST,ETHVNST,VBTCVNST").split(",")
DB_URL = os.getenv("DB_URL", "mysql+pymysql://user:password@127.0.0.1:3306/database")

engine = create_engine(DB_URL)

# NAMI API URL
API_URL = "https://datav2.nami.exchange/api/v1/chart/history?resolution=1h&broker=NAMI_SPOT&symbol={symbol}&from={start_time}&to={end_time}"
# Comma-separated list of token IDs, e.g. "cardano,liquidity-token,another-token"

# Ensure database and table exist
create_sql = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
  `open_time` int unsigned ,
  `open` double ,
  `high` double ,
  `low` double ,
  `close` double,
  `symbol` varchar(15),
)
"""


def fetch_prices(symbols=None, start_time=None, end_time=None):
    data =pd.DataFrame([], columns=["open_time", "open", "high", "low", "close", "symbol"])
    for symbol in symbols:
        url = API_URL.format(symbol=symbol, start_time=start_time, end_time=end_time)
        response = requests.get(url)
        if response.status_code == 200:
            json_data = response.json()
            df = pd.DataFrame(json_data, columns=["open_time", "open", "high", "low", "close", 'volumn','qty']
                              )[["open_time", "open", "high", "low", "close"]]
            df["symbol"] = symbol
            print(df)
            data = pd.concat([data, df], ignore_index=True)
            
        else:
            print(f"Error fetching data for {symbol}: {response.status_code}")
    
    try:
        data.to_sql(TABLE_NAME, con=engine, if_exists='append', index=False)
        print(f"Data for {symbol} inserted successfully.")
    except Exception as e:
        print(f"Error inserting data for {symbol}: {e}")


if __name__ == "__main__":
    end_time = int(datetime.now().replace(minute=0, second=0).timestamp())-1
    start_time = end_time - 3600 # * 24 * 90

    fetch_prices(symbol_list, start_time, end_time)

