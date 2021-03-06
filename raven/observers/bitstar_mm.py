import logging
from .observer import Observer
import json
import time
import os
from private_markets import bitstampusd,bitstarcny,huobicny,okcoincny
import math
import os, time
import sys
import traceback
import config
from .basicbot import BasicBot
import threading

class MarketMaker(BasicBot):
    exchange = 'BitstarCNY'
    out_dir = 'trade_history/'
    try:
        filename = exchange + config.ENV+ '.csv'
    except Exception as e:
        filename = exchange + '.csv'


    def __init__(self):
        super().__init__()

        self.clients = {
            # TODO: move that to the config file
            "BitstarCNY": bitstarcny.PrivateBitstarCNY(config.BITSTAR_API_KEY, config.BITSTAR_SECRET_TOKEN),
        }
        
        self.trade_timeout = 10  # in seconds

        self.cny_balance = 0
        self.btc_balance = 0
        self.cny_total = 0
        self.btc_total = 0

        self.bid_fee_rate = config.bid_fee_rate
        self.ask_fee_rate = config.ask_fee_rate
        self.bid_price_risk = config.bid_price_risk
        self.ask_price_risk = config.ask_price_risk

        self.peer_exchange ='StandardCNY'
        # self.peer_exchange ='HuobiCNY'

        try:
            os.mkdir(self.out_dir)
        except:
            pass

        self.clients[self.exchange].cancel_all()

        logging.info('MarketMaker Setup complete')
        # time.sleep(2)

    def terminate(self):
        super().terminate()
        
        self.clients[self.exchange].cancel_all()

        logging.info('terminate complete')

    def hedge_order(self, order, result):
        pass

    def market_maker(self, depths):
        kexchange = self.exchange

        # update price
        try:
            bid_price = int(depths[self.exchange]["bids"][0]['price'])
            ask_price =  int(depths[self.exchange]["asks"][0]['price'])
            bid_amount = (depths[self.exchange]["bids"][0]['amount'])
            ask_amount=  (depths[self.exchange]["asks"][0]['amount'])

            bid1_price = int(depths[self.exchange]["bids"][1]['price'])
            ask1_price =  int(depths[self.exchange]["asks"][1]['price'])
            peer_bid_price = int(depths[self.peer_exchange]["bids"][0]['price'])
            peer_ask_price = int(depths[self.peer_exchange]["asks"][0]['price'])

        except  Exception as ex:
            logging.warn("exception depths:%s" % ex)
            traceback.print_exc()
            return

        if bid_price == 0 or ask_price == 0 or peer_bid_price == 0 or peer_bid_price == 0:
            logging.warn("exception ticker")
            return

        if bid_price+1 < ask_price :
            buyprice = bid_price + 1
        else:
            buyprice = bid_price

        if ask_price-1 > bid_price:
            sellprice = ask_price - 1
        else:
            sellprice = ask_price

        if buyprice == sellprice:
            if buyprice > bid_price:
                buyprice -=1
            elif sellprice < ask_price:
                sellprice +=1

        peer_bid_hedge_price = int(peer_bid_price*(1+self.bid_fee_rate))
        peer_ask_hedge_price = int(peer_ask_price*(1-self.ask_fee_rate))

        buyprice=min(buyprice, peer_bid_hedge_price) - self.bid_price_risk
        sellprice=max(sellprice, peer_ask_hedge_price) + self.ask_price_risk
        logging.debug("sellprice/buyprice=(%s/%s)" % (sellprice, buyprice))

        self.buyprice = buyprice
        self.sellprice = sellprice

        # Update client balance
        self.update_balance()

        # query orders
        if self.is_buying():
            for buy_order in self.get_orders('buy'):
                logging.debug(buy_order)
                result = self.clients[kexchange].get_order(buy_order['id'])
                logging.debug(result)
                if not result:
                    logging.warn("get_order buy #%s failed" % (buy_order['id']))
                    return

                self.hedge_order(buy_order, result)

                if result['status'] == 'CLOSE' or result['status'] == 'CANCELED':
                    self.remove_order(buy_order['id'])
                else:
                    current_time = time.time()
                    if (result['price'] != buyprice) and \
                        ((result['price'] > peer_bid_hedge_price) or \
                        ( current_time - buy_order['time'] > self.trade_timeout and  \
                        (result['price'] < bid_price or result['price'] > (bid1_price + 1)))):
                        logging.info("[TraderBot] cancel last buy trade " +
                                     "occured %.2f seconds ago" %
                                     (current_time - buy_order['time']))
                        logging.info("cancel buyprice %s result['price'] = %s[%s]" % (buyprice, result['price'], result['price'] != buyprice))

                        self.cancel_order(kexchange, 'buy', buy_order['id'])


        if self.is_selling():
            for sell_order in self.get_orders('sell'):
                logging.debug(sell_order)
                result = self.clients[kexchange].get_order(sell_order['id'])
                logging.debug(result)
                if not result:
                    logging.warn("get_order sell #%s failed" % (sell_order['id']))
                    return

                self.hedge_order(sell_order, result)

                if result['status'] == 'CLOSE' or result['status'] == 'CANCELED':
                    self.remove_order(sell_order['id'])
                else:
                    current_time = time.time()
                    if (result['price'] != sellprice) and \
                        ((result['price'] < peer_ask_hedge_price) or \
                        (current_time - sell_order['time'] > self.trade_timeout and \
                            (result['price'] > ask_price or result['price'] < (ask1_price - 1)))):
                        logging.info("[TraderBot] cancel last SELL trade " +
                                     "occured %.2f seconds ago" %
                                     (current_time - sell_order['time']))
                        logging.info("cancel sellprice %s result['price'] = %s [%s]" % (sellprice, result['price'], result['price'] != sellprice))

                        self.cancel_order(kexchange, 'sell', sell_order['id'])
            
        # excute trade
        if self.buying_len() < config.MAKER_BUY_QUEUE:
            self.new_order_notify(kexchange, 'buy')
        if self.selling_len() < config.MAKER_SELL_QUEUE:
            self.new_order_notify(kexchange, 'sell')

    def update_trade_history(self, time, price, cny, btc):
        filename = self.out_dir + self.filename
        need_header = False

        if not os.path.exists(filename):
            need_header = True

        fp = open(filename, 'a+')

        if need_header:
            fp.write("timestamp, price, cny, btc\n")

        fp.write(("%d") % time +','+("%.2f") % price+','+("%.2f") % cny+','+ str(("%.4f") % btc) +'\n')
        fp.close()

    def update_balance(self):
        for kclient in self.clients:
            if kclient == self.exchange:
                self.clients[kclient].get_info()
                self.cny_balance = self.clients[kclient].cny_balance
                self.btc_balance = self.clients[kclient].btc_balance
                
                self.cny_frozen = self.clients[kclient].cny_frozen
                self.btc_frozen = self.clients[kclient].btc_frozen

        cny_abs = abs(self.cny_total - self.cny_balance_total(self.buyprice))
        cny_diff = self.cny_total*0.1
        btc_abs = abs(self.btc_total - self.btc_balance_total(self.sellprice))
        btc_diff = self.btc_total*0.1

        self.cny_total = self.cny_balance_total(self.buyprice)
        self.btc_total = self.btc_balance_total(self.sellprice)

        if (cny_abs > 5 and cny_abs < cny_diff) or (btc_abs > 0.001 and btc_abs < btc_diff):
            logging.debug("update_balance-->")
            self.update_trade_history(time.time(), self.buyprice, self.cny_total, self.btc_total)

        logging.debug("cny_balance=%s/%s, btc_balance=%s/%s, total_cny=%0.2f, total_btc=%0.2f", 
            self.cny_balance, self.cny_frozen, self.btc_balance, self.btc_frozen, 
            self.cny_balance_total(self.buyprice), self.btc_balance_total(self.sellprice))

    def cny_balance_total(self, price):
        return self.cny_balance + self.cny_frozen+ (self.btc_balance + self.btc_frozen)* price
    
    def btc_balance_total(self, price):
        return self.btc_balance + self.btc_frozen  + (self.cny_balance +self.cny_frozen ) / (price*1.0)

    def new_order_notify(self, kexchange, type, maker_only=True, amount=None, price=None):
        order = super().new_order(kexchange, type, maker_only, amount, price)
        
        if order:
            # self.notify_msg(order['type'], order['price'])
            t = threading.Thread(target = self.notify_msg, args=(order['type'], order['price'],))
            t.start()
            logging.info("current has %d threads" % (threading.activeCount() - 1))

    def begin_opportunity_finder(self, depths):
        self.market_maker(depths)

    def end_opportunity_finder(self):
        pass

    def opportunity(self, profit, volume, buyprice, kask, sellprice, kbid, perc, weighted_buyprice, weighted_sellprice):
        pass
