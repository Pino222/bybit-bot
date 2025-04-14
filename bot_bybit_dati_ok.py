from flask import Flask
from threading import Thread
import time
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import AverageTrueRange
from pybit.unified_trading import HTTP
import requests
import datetime
import csv
import os
import schedule
import threading

# === CONFIGURAZIONE ===
API_KEY = "DZmHwodLxWTX0GPnSq"
API_SECRET = "jMnqTFVJNOIeZp93MyaLRWpieGWvehhHcoh8"
TELEGRAM_TOKEN = "7522120442:AAEkVESFOELthMc-PgTvyBiygCzrnOEn2WM"
TELEGRAM_CHAT_ID = "645022149"
COPPIE = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]
LEVA = 5
CAPITALE_USDT = 50
INTERVALLO = "15"
LOG_FILE = "trades_log.csv"

# === FLASK APP ===
app = Flask('')

@app.route('/')
def home():
    return "✅ Il bot è attivo!"

@app.route("/dashboard")
def dashboard():
    try:
        dati_coppie = []
        for symbol in COPPIE:
            df = get_candles(symbol)
            if df is not None:
                df = analizza(df)
                ultimo = {
                    "symbol": symbol,
                    "RSI": round(df["RSI"].iloc[-1], 2),
                    "MACD": round(df["MACD"].iloc[-1], 4),
                    "Signal": round(df["MACD_signal"].iloc[-1], 4),
                    "EMA12": round(df["EMA12"].iloc[-1], 2),
                    "EMA26": round(df["EMA26"].iloc[-1], 2)
                }
                dati_coppie.append(ultimo)

        html = "<h2>📊 Dati Tecnici Live</h2><table border='1'><tr><th>Coppia</th><th>RSI</th><th>MACD</th><th>Signal</th><th>EMA12</th><th>EMA26</th></tr>"
        for row in dati_coppie:
            html += f"<tr><td>{row['symbol']}</td><td>{row['RSI']}</td><td>{row['MACD']}</td><td>{row['Signal']}</td><td>{row['EMA12']}</td><td>{row['EMA26']}</td></tr>"
        html += "</table>"
        return html
    except Exception as e:
        return f"<p>Errore nel caricamento dati: {e}</p>"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# === SESSIONE BYBIT ===
session = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET,
    testnet=False
)

def invia_messaggio(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        r = requests.post(url, data=data)
        r.raise_for_status()
        print("[Telegram] Inviato:", msg)
    except Exception as e:
        print("[Telegram] Errore:", e)

def log_trade(symbol, side, entry_price, exit_price, profit, timestamp):
    with open(LOG_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([timestamp, symbol, side, entry_price, exit_price, profit])

def get_candles(symbol):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=INTERVALLO, limit=100)
        df = pd.DataFrame(res['result']['list'], columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df = df.astype(float)
        return df
    except Exception as e:
        print(f"[Errore] Dati {symbol}: {e}")
        return None

def analizza(df):
    df['RSI'] = RSIIndicator(df['close']).rsi()
    df['MACD'] = MACD(df['close']).macd()
    df['MACD_signal'] = MACD(df['close']).macd_signal()
    df['EMA12'] = EMAIndicator(df['close'], window=12).ema_indicator()
    df['EMA26'] = EMAIndicator(df['close'], window=26).ema_indicator()
    return df

def calcola_volatilita(df):
    atr = AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=14).average_true_range()
    df["volatilita"] = atr
    return df

def genera_segnale_auto(df):
    df = calcola_volatilita(df)
    rsi_value = df["RSI"].iloc[-1]
    macd = df["MACD"].iloc[-1]
    macd_signal = df["MACD_signal"].iloc[-1]
    ema12 = df["EMA12"].iloc[-1]
    ema26 = df["EMA26"].iloc[-1]
    vol = df["volatilita"].iloc[-1]

    rsi_low = 30 if vol < 50 else 25
    rsi_high = 70 if vol < 50 else 75

    if rsi_value < rsi_low and macd > macd_signal and ema12 > ema26:
        return "long"
    elif rsi_value > rsi_high and macd < macd_signal and ema12 < ema26:
        return "short"
    return None

def aggiorna_trailing_stop(symbol, entry_price, side):
    try:
        posizione = session.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
        info = session.get_instruments_info(category="linear", symbol=symbol)
        current_price = float(info["result"]["list"][0].get("lastPrice", 0))
        entry_price_raw = posizione["avgPrice"]
        if not entry_price_raw or current_price == 0:
            print(f"[{symbol}] Nessun prezzo valido, SL non aggiornato.")
            return
        entry_price = float(entry_price_raw)
        size = float(posizione["size"])
        side = posizione["side"]
        if size == 0:
            return
        profit_threshold = 0.05
        sl_percentage = 0.10
        if side == "Buy":
            gain = (current_price - entry_price) / entry_price
            if gain >= profit_threshold:
                nuovo_sl = current_price - (entry_price * sl_percentage)
                session.set_trading_stop(category="linear", symbol=symbol, stopLoss=round(nuovo_sl, 2))
                log_trade(symbol, "LONG", entry_price, current_price, (current_price - entry_price) * size, datetime.datetime.utcnow().isoformat())
                print(f"[Trailing Stop] LONG {symbol}: SL aggiornato a {nuovo_sl}")
        elif side == "Sell":
            gain = (entry_price - current_price) / entry_price
            if gain >= profit_threshold:
                nuovo_sl = current_price + (entry_price * sl_percentage)
                session.set_trading_stop(category="linear", symbol=symbol, stopLoss=round(nuovo_sl, 2))
                log_trade(symbol, "SHORT", entry_price, current_price, (entry_price - current_price) * size, datetime.datetime.utcnow().isoformat())
                print(f"[Trailing Stop] SHORT {symbol}: SL aggiornato a {nuovo_sl}")
    except Exception as e:
        print(f"[Trailing Stop] Errore: {e}")

def loop_trading():
    while True:
        for symbol in COPPIE:
            df = get_candles(symbol)
            if df is None:
                print(f"[{symbol}] Nessun dato ricevuto.")
                continue
            print(f"{symbol}: {len(df)} candele ricevute")  # Debug
            if len(df) < 30:
                print(f"[{symbol}] Dati insufficienti, salto...")
                continue

            df = analizza(df)
            segnale = genera_segnale_auto(df)
            ora = datetime.datetime.now().strftime('%H:%M:%S')
            print(f"[{ora}] {symbol} - Segnale: {segnale or 'Nessuno'}")
            aggiorna_trailing_stop(symbol, 0, "")
        time.sleep(60 * 15)

def start_bot():
    try:
        invia_messaggio("🤖 Bot trading avviato con successo!")
        loop_trading()
    except Exception as e:
        print(f"[BOT] Errore: {e}")
        invia_messaggio(f"❌ Errore nel bot: {e}")

if __name__ == "__main__":
    keep_alive()
    start_bot()
