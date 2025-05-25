from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine 
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.exceptions import TransactionNotFound, ContractLogicError
from web3.types import HexStr
# from . import config
import time, json, requests
import sys, os, logging
import numpy as np
from datetime import datetime 

load_dotenv()
symbol_list  = os.getenv("SYMBOLS", "USDTVNST,ETHVNST,VBTCVNST").split(",")
DB_URL = os.getenv("DB_URL", "mysql+pymysql://user:password@127.0.0.1:3306/database")

RPC = os.getenv("RPC", "https://bsc-pokt.nodies.app")
SWAP_ROUNTER_ADDRESS = os.getenv("SWAP_ROUNTER_ADDRESS")
SWAP_ROUNTER_ABI = os.getenv("SWAP_ROUNTER_ABI")
FACTORY_ADDRESS = os.getenv("FACTORY_ADDRESS")
FACTORY_ABI = os.getenv("FACTORY_ABI")
WALLET = json.loads(os.getenv("WALLET"))
token_info = json.loads(os.getenv("TOKEN_INFO"))

engine = create_engine(DB_URL)


# Connect to Metis RPC of POKT
web3_gateway = Web3(Web3.HTTPProvider(RPC))
# Check if connected to Metis
if not web3_gateway.is_connected():
    print("Failed to connect")


swapContract = web3_gateway.eth.contract(
    address=Web3.to_checksum_address(SWAP_ROUNTER_ADDRESS),
    abi=SWAP_ROUNTER_ABI)

wallet = [Web3.to_checksum_address(WALLET[0][0]), WALLET[0][1]]

router = web3_gateway.eth.contract(
    address=Web3.to_checksum_address(SWAP_ROUNTER_ADDRESS),
    abi=SWAP_ROUNTER_ABI
)
factory = web3_gateway.eth.contract(
    address=Web3.to_checksum_address(FACTORY_ADDRESS),
    abi=FACTORY_ABI
)

@lru_cache()
def get_decimal(symbol: str):
    try:
        token = web3_gateway.eth.contract(
            address=token_info[symbol][0], 
            abi=token_info[symbol][1]
        )
        res = token.functions.decimals().call()
    except Exception as e:
        print(e)
        return 18
    return res

@lru_cache()
def to_wei(symbol: str, amount: float):
    decimal = get_decimal(symbol)
    return int(amount * 10**decimal)

@lru_cache()
def from_wei(symbol: str, amount: int):
    decimal = get_decimal(symbol)
    return amount / 10**decimal


class DEXSwapBot():
    def __init__(self, name:str, gateway, swap_router, swap_factory, wallet:tuple, pair:str, token_info:dict, native_token='WBNB'):
        # self.id = -> for save and load in DB
        self.name = name
        self.invest_balance = [0,0]
        self.total_invest = 0  # lock token amount in wei
        self.gateway = gateway
        self.router_contract = swap_router
        self.factory_contract = swap_factory
        self.walletAddress = Web3.to_checksum_address(wallet[0])
        self.privateKey = wallet[1]
        self.token_info = token_info
        for k, v in self.token_info.items():
            self.token_info[k] = (Web3.to_checksum_address(v[0]), v[1])
        self.pair = pair # try get more pair[0] token from trade pair
        self.native_token = native_token
        self.logger = logging.getLogger("name")  # Logger
        self.gas_limit = 200000
    
    def _wait_for_receipt(self, txn_hash):
        receipt = None
        # print('Waiting for receipt')
        for i in range(0, 120):
            try:
                receipt = self.gateway.eth.get_transaction_receipt(txn_hash)
            except TransactionNotFound:
                time.sleep(0.5)
                continue
            if receipt:
                return receipt

    def swap(self, pair:list=['WBNB','USDT'], amount_in:int=1000000000000000000, amount_out_min:int=0):
        if amount_in < 0:
            raise Exception('Invalid amount_in')
        if amount_out_min <= 0:
            amount_out_min = 1
        
        sell_token, buy_token = pair
        try:
            sell_token_address, sell_token_abi = self.token_info.get(sell_token)
            buy_token_address, buy_token_abi = self.token_info.get(buy_token)
            nt_add, nt_abi = self.token_info[self.native_token]
        except Exception:
            raise Exception('Buy or sell token not found!')
        sell_token_contract = self.gateway.eth.contract(sell_token_address, abi=sell_token_abi)

        symbol = sell_token_contract.functions.symbol().call()

        # Get valid path
        path, amounts_out = self.estimate(pair, amount_in)
        # print(f"Path: {path}", amounts_out)

        # Check and approve sell token if necessary
        allowance = self.get_allowance(symbol)
        # print("allowance - amount_in", Web3.from_wei(allowance,'ether'), Web3.from_wei(amount_in,'ether')) 
        if allowance < amount_in:
            self.approve_token(symbol)

        # Get current gas price
        gas_price = self.gateway.eth.gas_price  # Returns gas price in wei
        # Init transaction parameters
        tx_params = {
            'from': self.walletAddress,
            'nonce': self.gateway.eth.get_transaction_count(self.walletAddress),
            'gas': self.gas_limit, # requre for testnet - Optional: Add gas limit in BNB <= 0.01$ (need convert usdt -> bnb to get gas limit)
            # Optional: Add gas price if needed
            'gasPrice': gas_price
        }
        txn = self.router_contract.functions.swapExactTokensForTokens(
            amount_in,
            amount_out_min,
            path,
            self.walletAddress,
            int(datetime.now().timestamp()) + 60 * 20 
            # self.gateway.eth.get_block('latest')['timestamp'] + 60 * 20 
        )

        build_txn = txn.build_transaction(tx_params)
        # Estimate gas
        gas_estimate = self.gateway.eth.estimate_gas(build_txn)

        # Calculate total transaction cost
        total_gas_cost_est = gas_estimate * gas_price  # Gas estimate × gas price (in wei)
        # buy_gas_if_need([symbol, self.native_token], [self.walletAddress, self.privateKey], 0.01, total_gas_cost_est,
        #                 swap=[self.router_contract.address, self.router_contract.abi])


        # Send transaction
        sent_txn = None
        for i in range(10):
            try:
                # Signing transaction
                signed_txn = self.gateway.eth.account.sign_transaction(build_txn, private_key=self.privateKey)
                sent_txn = self.gateway.eth.send_raw_transaction(signed_txn.raw_transaction)
                break
            except Exception as e:
                time.sleep(0.5)
                tx_params.update({"nonce": self.gateway.eth.get_transaction_count(self.walletAddress)})
                build_txn = txn.build_transaction(tx_params)
                pass
        txn_hash = Web3.to_hex(sent_txn)

        # Confirm transaction completion
        receipt = self._wait_for_receipt(txn_hash)

        amount_in = None
        amount_out = None
        if receipt:
            # Swap event signature
            swap_event_signature = Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()           
            for log in receipt['logs']:
                if log['topics'][0].hex() == swap_event_signature:
                    hex_str = HexStr(log['data'].hex())
                    # print(hex_str)
                    # print(type(hex_str))
                    # print('----------------')
                    decoded_data = Web3.to_bytes(hexstr=hex_str)

                    # print(decoded_data)
                    # print(type(decoded_data))
                    # print('----------------')
                    amount_in = int.from_bytes(decoded_data[32:64], "big")  # Token sold
                    amount_out = int.from_bytes(decoded_data[64:96], "big")  # Token bought
                    break
        
        if(pair[0] == self.pair[0]) and (pair[1] == self.pair[1]):
            self.invest_balance[0] -= amount_in
            self.invest_balance[1] += amount_out
        elif (pair[0] == self.pair[1]) and (pair[1] == self.pair[0]):
            self.invest_balance[0] += amount_out
            self.invest_balance[1] -= amount_in
        else:
            raise Exception('Invalid pair')

        result = {
            'bot': self.name,
            'time': int(time.time()), 
            'self_token': pair[0],
            'buy_token': pair[1],
            'amount_in': amount_in,
            'amount_out': amount_out,
            'txn_hash': txn_hash
            }

        return result

    def get_allowance(self, symbol: str):
        if self.gateway is None:
            raise Exception('No self.gateway found')
        t_add, t_abi = self.token_info[symbol]
        token_contract = self.gateway.eth.contract(address=t_add, abi=t_abi)
        allowance = token_contract.functions.allowance(self.walletAddress, self.router_contract.address).call()
        # print(f"Allowance (in get_allowance): {allowance}")
        return allowance  # Web3.to_wei(allowance, "ether")

    def approve_token(self, symbol: str, amount: int = 10e6):
        amount = int(amount)
        if self.gateway is None:
            raise Exception('No self.gateway found')
        # print(f"Approving {symbol} token", amount)
        t_add, t_abi = self.token_info[symbol]
        token_contract = self.gateway.eth.contract(address=t_add, abi=t_abi)
        approve_txn = token_contract.functions.approve(
            self.router_contract.address,  # Router contract address
            Web3.to_wei(amount, 'ether')  # Amount to approve
        ).build_transaction({
            "from": self.walletAddress,
            "nonce": self.gateway.eth.get_transaction_count(self.walletAddress),
            "gasPrice": self.gateway.eth.gas_price,
        })
        signed_approve_txn = self.gateway.eth.account.sign_transaction(approve_txn, private_key=self.privateKey)
        tx = self.gateway.eth.send_raw_transaction(signed_approve_txn.raw_transaction)
        return tx

    def gas_withdraw(self, sender: tuple | list, amount: int = 0.01):
        if self.gateway is None:
            raise Exception('No self.gateway found')
        sender_address, private_key = sender

        
        nt_add, nt_abi = self.token_info[self.native_token]
        token_contract = self.gateway.eth.contract(address=nt_add, abi=nt_abi)
        withdraw_txn = token_contract.functions.withdraw(
            Web3.to_wei(amount, "ether")
        ).build_transaction({
            'from': sender_address,
            'gasPrice': self.gateway.eth.gas_price,
            'nonce': self.gateway.eth.get_transaction_count(sender_address)
        })
        signed_txn = self.gateway.eth.account.sign_transaction(withdraw_txn, private_key=private_key)
        tx = self.gateway.eth.send_raw_transaction(signed_txn.raw_transaction)
        return tx

    def buy_gas_if_need(self, pair: list, sender: tuple | list, gas_to_buy: int | float, min_gas: int | float, swap=None):
        if self.gateway is None:
            raise Exception('No self.gateway found')
        if swap is None:
            raise Exception('No swap router found')

        txs = []
        sender_address, private_key = sender
        swap_address, swap_abi = swap

        
        t_add, t_abi = self.token_info[pair[-1]]
        token_contract = self.gateway.eth.contract(address=t_add, abi=t_abi)
        balance = token_contract.functions.balanceOf(sender_address).call()
        # print(f"Balance: {balance}")
        if balance >= min_gas:
            # print(f"{sender_address} have enough {pair[-1]} balance")
            return None
        if len(pair) > 1:
            swap_txn = self.router_contract.functions.swapTokensForExactTokens(
                amountOut=Web3.to_wei(gas_to_buy, 'ether'),
                amountInMax=100000,  # todo: fix by check amount in * 20%
                path=[self.token_info[pair[0]][0], self.token_info[pair[1]][0]],
                to=sender_address,
                deadline=(int(time.time() + 1000000))
            ).build_transaction({
                'from': sender_address,
                'gasPrice': self.gateway.eth.gas_price,
                'nonce': self.gateway.eth.get_transaction_count(sender_address)
            })
            signed_txn = self.gateway.eth.account.sign_transaction(swap_txn, private_key=private_key)
            tx = self.gateway.eth.send_raw_transaction(signed_txn.raw_transaction)
            print("Swap txn: ", tx)
            txs.append(tx)

        tx = self.gas_withdraw(sender, gas_to_buy, pair[-1])
        txs.append(tx)
        return txs

    def get_token_decimal(self, decimal):
        decimal = int("1" + str("0" * decimal))
        decimals_dict = {"wei": 1,
                        "kwei": 1000,
                        "babbage": 1000,
                        "femtoether": 1000,
                        "mwei": 1000000,
                        "lovelace": 1000000,
                        "picoether": 1000000,
                        "gwei": 1000000000,
                        "shannon": 1000000000,
                        "nanoether": 1000000000,
                        "nano": 1000000000,
                        "szabo": 1000000000000,
                        "microether": 1000000000000,
                        "micro": 1000000000000,
                        "finney": 1000000000000000,
                        "milliether": 1000000000000000,
                        "milli": 1000000000000000,
                        "ether": 1000000000000000000,
                        "kether": 1000000000000000000000,
                        "grand": 1000000000000000000000,
                        "mether": 1000000000000000000000000,
                        "gether": 1000000000000000000000000000,
                        "tether": 1000000000000000000000000000000}

        # list out keys and values separately
        key_list = list(decimals_dict.keys())
        val_list = list(decimals_dict.values())

        # print key with val 100
        position = val_list.index(decimal)
        return key_list[position]

    def estimate(self, t_path, amount_in_wei, native_token='WBNB'):
        # print("estimate: ", t_path, amount_in_wei)
        if amount_in_wei == 0:
            return [], [0]
        nt_add = self.token_info[native_token][0]
        valid_path = [self.token_info.get(t_path[0])[0]]
        for t in t_path[1:]:
            token2 = self.token_info.get(t)[0]
            pair_address = self.factory_contract.functions.getPair(
                valid_path[-1], 
                token2
                ).call()
            if int(pair_address, 16) != 0:
                valid_path.append(token2)
            else:
                pair_add1 = self.factory_contract.functions.getPair(
                    valid_path[-1], 
                    nt_add
                    ).call()
                pair_add2 = self.factory_contract.functions.getPair(
                    nt_add,
                    token2, 
                    ).call()
                if int(pair_add1, 16)*int(pair_add2, 16) != 0:
                    valid_path.append(nt_add)
                    valid_path.append(token2)
                else:
                    raise Exception(f"valid path not found")
        amounts = self.router_contract.functions.getAmountsOut(amount_in_wei, valid_path).call()
        return valid_path, amounts

    def _check_wallet_balance(self):
        token_contract=self.gateway.eth.contract(
            address=self.token_info[self.pair[0]][0],
            abi=self.token_info[self.pair[0]][1],
            )
        t1_balance = token_contract.functions.balanceOf(self.walletAddress).call()
        
        token_contract=self.gateway.eth.contract(
            address=self.token_info[self.pair[1]][0],
            abi=self.token_info[self.pair[1]][1],
            )
        t2_balance = token_contract.functions.balanceOf(self.walletAddress).call()

        path, amounts_out = self.estimate(self.pair[::-1], t2_balance)
        return [t1_balance, [t2_balance, amounts_out[-1]]]
    
    def deposite(self, amount_in_wei, invest_balance:list=None):
        self.invest_balance[0] += amount_in_wei  # increase amount that bot can use for trading
        self.total_invest += amount_in_wei
        self.invest_balance = invest_balance if invest_balance else [amount_in_wei, 0]
        result = {
                'bot': self.name,
                'time': int(time.time()), 
                'sell_token': self.pair[0],
                'amount_in': amount_in_wei
                }
        return result

    def withdraw(self, amount_in_wei, recipient:str=None):
        amount = min(self.invest_balance[0], amount_in_wei)
        self.invest_balance[0] -= amount        
        self.total_invest = int(np.ceil(amount/self.get_invest_value() * self.total_invest))  # reduce total invest amount Proportion to amount withdraw / total value
        
        result = {
                'bot': self.name,
                'time': int(time.time()), 
                'buy_token': self.pair[0],
                'amount_out': amount
                }
        if recipient:
            token_contract=self.gateway.eth.contract(
                address=self.token_info[self.pair[0]][0],
                abi=self.token_info[self.pair[0]][1],
                )
            gasprice = self.gateway.eth.gas_price
            # Build the transaction
            transfer_txn = token_contract.functions.transfer(
                recipient,  # recipient (address)
                amount      # amount (uint256) 
            ).build_transaction({
                'chainId': self.gateway.eth.chain_id,
                'gas': self.gas_limit,  # Adjust the gas limit as needed
                'gasPrice': gasprice,  # Adjust the gas price as needed or use w3.eth.generate_gas_price()
                'nonce': self.gateway.eth.get_transaction_count(self.walletAddress), 
            })
            # Sign the transaction with the private key
            signed_txn = self.gateway.eth.account.sign_transaction(transfer_txn, self.privateKey)
            sent_txn = self.gateway.eth.send_raw_transaction(signed_txn.raw_transaction)
            txn_hash = Web3.to_hex(sent_txn)
            # Confirm transaction completion
            receipt = self._wait_for_receipt(txn_hash)
            result.update({
                'txn_hash': txn_hash,
            })
        return result

    def get_invest_value(self):
        p, a = self.estimate(self.pair[::-1], self.invest_balance[1])
        value2 = a[-1]
        value = self.invest_balance[0] + value2
        return value

    def getROI(self):
        # return profit / invest
        if self.total_invest == 0:
            return 0        
        return (self.get_invest_value()-self.total_invest) / self.total_invest * 100
    
    def get_trade_decision(self, phrase:str=None) -> tuple:
        # set your trade strategy here
        # get decision from vistiaAI 
        order_pair = None
        amount = 0
        if not phrase:
            res = requests.get("https://api.vistia.co/api/v2/al-trade/top-over-sold/v2.2?heatMapType=rsi7&interval=5m")
            if res.status_code == 200:
                for s in res.json():
                    if s['symbol'] == ''.join(self.pair): # buy phase
                        phrase = 'buy'
                        break
            if order_pair is None:
                res = requests.get("https://api.vistia.co/api/v2/al-trade/top-over-bought/v2.2?heatMapType=rsi7&interval=5m")
                if res.status_code == 200:
                    for s in res.json():
                        if s['symbol'] == ''.join(self.pair[::-1]): # sell phase
                            phrase = 'sell'
                            break

        if phrase.lower() == 'buy':
            order_pair = self.pair
            amount = int(self.invest_balance[0] * 0.3)
        elif phrase.lower() == 'sell':
            order_pair = self.pair[::-1]
            amount = int(self.invest_balance[1] * 0.8)
        else:
            return (self.pair, 0)
        return (order_pair, amount)
        
        return (self.pair[::-1], 1390851606935088217)

    def run(self, phrase:str=None):
        order_pair, amount = self.get_trade_decision(phrase)

        if order_pair is not None and amount > 0:
            result = self.swap(pair=order_pair, amount_in=amount, amount_out_min=1)
            self.logger.info(result)
            # amount_in = Web3.from_wei(result['amount_in'], 'ether')        
            amount_in = from_wei(order_pair[0], result['amount_in'] or amount)        

            amount_out = from_wei(order_pair[0], result['amount_out'])            
            print(f"{self.name} trade {amount_in} {order_pair[0]} for {amount_out} {order_pair[1]} \ntx: {result['txn_hash']}")
        else:
            print('not on good time for trade')

class BotManager():
    def __init__(self, bots: list=[], db_connect=None, 
                 wallet=["",""], gateway=None, 
                 swap_router=None, swap_factory=None, token_info=None):
        # self.id = -> for save and load in DB
        self.bots = bots
        self.db_connect = db_connect
        self.wallet = wallet
        self.gateway = gateway
        self.swap_router = swap_router
        self.swap_factory = swap_factory
        self.token_info = token_info

    def run(self):
        for bot in self.bots:
            bot.run()
            self.save_bot_state(bot.name)
            
    def add_bot(self, bot):
        self.bots.append(bot)

    def remove_bot(self, bot):
        self.bots.remove(bot)

    def get_bot(self, name):
        for bot in self.bots:
            if bot.name == name:
                return bot
        return None

    def get_all_bot(self):
        return self.bots

    def get_bot_state(self, name):
        bot = self.get_bot(name)
        if bot:
            return {
                'name': bot.name,
                'pair': bot.pair,
                'invest_balance': bot.invest_balance,
                'total_invest': bot.total_invest,
                'roi': bot.getROI()
            }
        return None

    def get_all_bot_state(self):
        states = []
        for bot in self.bots:
            states.append({
                'name': bot.name,
                'pair': bot.pair,
                'invest_balance': bot.invest_balance,
                'total_invest': bot.total_invest,
                'roi': bot.getROI()
            })
        return states

    def get_all_bot_name(self):
        names = []
        for bot in self.bots:
            names.append(bot.name)
        return names
    
    def save_bot_state(self, bot_name=None):
        cur = con.cursor()
        bots = self.bots if bot_name is None else [self.get_bot(bot_name)]
        ts = int(time.time())
        # res = cur.execute("""CREATE TABLE IF NOT EXISTS bot_report(time, name, address,token_1,token_2,amount_1,amount_2,invert,roi)""")
        for bot in bots:
            res = cur.execute(f"""
                INSERT INTO bot_report (time,name,address,token_1,token_2,amount_1,amount_2,invert,roi)
                VALUES (
                    {ts},
                    '{bot.name}',
                    '{bot.walletAddress}',
                    '{bot.pair[0]}', 
                    '{bot.pair[1]}', 
                    {bot.invest_balance[0]},
                    {bot.invest_balance[1]},
                    {bot.total_invest},
                    {bot.getROI():.2f}
                )
            """)
            con.commit()

    def load_bot(self, name):
        cur = self.db_connect.cursor()
        data = []
    
        res = cur.execute(f"""SELECT * FROM bot_report WHERE name = "{name}" order by time desc limit 1""")
        state = res.fetchone()
        cur.close()

        bot = self.get_bot(name)
        if bot is None:
            bot = DEXSwapBot(
                name=name,
                gateway=self.gateway,
                wallet=self.wallet, 
                swap_router=self.swap_router, 
                swap_factory=self.swap_factory, 
                pair=[state[3], state[4]], 
                token_info=self.token_info)
            bot.invest_balance = [int(state[5]), int(state[6])]
            bot.total_invest = int(state[7])
            self.bots.append(bot)            
        else:
            bot.pair = [state[3], state[4]]
            bot.invest_balance = [int(state[5]), int(state[6])]
            bot.total_invest = int(state[7])
        return bot

    def allocate_funding(self):
        # bots_name = []
        rois = []
        invests = [] 
        for bot in self.bots:
            # bots_name.append(bot.name)
            rois.append(bot.getROI())
            invests.append(bot.total_invest)
        t_i = sum(invests)
        print("Total invest: ", Web3.from_wei(t_i, 'ether'))
        total_return = sum(rois) + len(rois)*100
        re_alloc_invest = [(100+p)*t_i / total_return for p in rois]
        print("realloc invest:")
        for i, bot in enumerate(self.bots):
            change = bot.total_invest - re_alloc_invest[i]
            if change>0:
                # print("withdraw")
                bot.withdraw(change)
            else:
                # print("deposite")
                bot.deposite(-change)
            print(f" {i}. Bot {bot.name} funding: {Web3.from_wei(bot.total_invest, 'ether')}") 


if __name__ == "__main__":
    command = "run" 
    print("""All command:
 - list
 - load {bot_name}
 - run
 - report
 - reallocate """)
    bot = DEXSwapBot(
        name="Bot_ADA-USDT",
        gateway=web3_gateway,
        wallet=wallet, 
        swap_router=router, 
        swap_factory=factory, 
        pair=['USDT','ADA'], 
        token_info=token_info,
    )
    bot.deposite(to_wei('USDT',7), [to_wei('USDT',5),to_wei('ADA',2.78661892)])
    bots = [bot]
    
    bm = BotManager(
        bots=bots,
        db_connect=engine,
        wallet=wallet,
        gateway=web3_gateway,
        swap_router=router,
        swap_factory=factory,
        token_info=token_info,
    )
    # res = bot.swap(pair=['USDT', 'ADA'], amount_in=amount_in, amount_out_min=1)
    while (command.lower() != "exit"):
        print("____________________________________________")
        print(bm.get_all_bot_state())
        command = input().strip()
        bm.get

        bot.run(command)



