import time
import requests
import schedule
import json
import os
import math
import statistics
from datetime import datetime, timezone

# ====================== НАЛАШТУВАННЯ ======================
TELEGRAM_TOKEN = "8736851302:AAFSkK7p7wCGA8u0K2Wz0L_ECE8-OF4qL30"
CHAT_ID = "399859535"

MIN_EDGE = 0.04
MIN_CONFIDENCE = 60
EARLY_SECONDS = 60
STATS_FILE = "stats.json"
# ==========================================================

def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "signals": []          # історія останніх сигналів
    }

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Помилка Telegram:", e)

def get_current_window():
    now = int(time.time())
    start = now - (now % 300)
    end = start + 300
    return start, end, end - now

def get_binance_klines(limit=40):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": "ETHUSDT", "interval": "1m", "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=5)
        return r.json()
    except:
        return []

def calculate_volatility(klines):
    if len(klines) < 10:
        return 0.0015
    closes = [float(k[4]) for k in klines]
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    return statistics.stdev(returns) if len(returns) > 1 else 0.0015

def get_polymarket_market(window_start: int):
    slug = f"eth-updown-5m-{window_start}"
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    try:
        r = requests.get(url, timeout=8)
        data = r.json()
        if data:
            return data[0]
    except:
        pass
    return None

def get_orderbook(token_id: str):
    url = f"https://clob.polymarket.com/book?token_id={token_id}"
    try:
        r = requests.get(url, timeout=5)
        return r.json()
    except:
        return None

def calculate_obi(book: dict, levels=5):
    if not book or "bids" not in book or "asks" not in book:
        return 0.0
    bids = book["bids"][:levels]
    asks = book["asks"][:levels]
    bid_vol = sum(float(b["size"]) for b in bids)
    ask_vol = sum(float(a["size"]) for a in asks)
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total

def get_best_prices(book: dict):
    if not book or not book.get("bids") or not book.get("asks"):
        return None, None, None
    best_bid = float(book["bids"][0]["price"])
    best_ask = float(book["asks"][0]["price"])
    mid = (best_bid + best_ask) / 2
    return best_bid, best_ask, mid

def check_resolved_signals(stats):
    """Перевіряємо результати минулих сигналів"""
    updated = False
    for sig in stats["signals"]:
        if sig.get("resolved"):
            continue
        
        # Чекаємо мінімум 40 секунд після кінця вікна
        if time.time() < sig["window_end"] + 40:
            continue
        
        market = get_polymarket_market(sig["window_start"])
        if not market:
            continue
        
        # Перевіряємо чи ринок закритий і є результат
        outcome_prices = market.get("outcomePrices")
        if not outcome_prices:
            continue
        
        try:
            prices = eval(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            # prices[0] = Up, prices[1] = Down
            if float(prices[0]) > 0.9:
                real_result = "UP"
            elif float(prices[1]) > 0.9:
                real_result = "DOWN"
            else:
                continue  # ще не розв'язано
        except:
            continue
        
        # Записуємо результат
        sig["resolved"] = True
        sig["real_result"] = real_result
        sig["win"] = (sig["side"] == real_result)
        
        stats["total"] += 1
        if sig["win"]:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        
        updated = True
        
        # Повідомлення про результат
        result_emoji = "✅" if sig["win"] else "❌"
        msg = f"{result_emoji} <b>Результат сигналу</b>\n"
        msg += f"Вікно: {sig['window_str']}\n"
        msg += f"Сигнал: <b>{sig['side']}</b>\n"
        msg += f"Реальність: <b>{real_result}</b>\n"
        msg += f"{'Виграш' if sig['win'] else 'Програш'}"
        send_telegram(msg)
    
    if updated:
        # Залишаємо тільки останні 50 сигналів
        stats["signals"] = stats["signals"][-50:]
        save_stats(stats)
    
    return stats

def analyze():
    stats = load_stats()
    stats = check_resolved_signals(stats)
    
    window_start, window_end, seconds_left = get_current_window()
    
    if seconds_left < 20:
        return
    
    klines = get_binance_klines(40)
    if not klines:
        return
    
    current_price = float(klines[-1][4])
    
    # Price to Beat
    ptb = None
    for k in reversed(klines):
        open_time = int(k[0]) // 1000
        if open_time <= window_start:
            ptb = float(k[1])
            break
    if ptb is None:
        ptb = float(klines[-5][1])
    
    volatility = calculate_volatility(klines)
    move = current_price - ptb
    
    market = get_polymarket_market(window_start)
    if not market:
        return
    
    try:
        token_ids = eval(market["clobTokenIds"])
        up_token, down_token = token_ids[0], token_ids[1]
    except:
        return
    
    up_book = get_orderbook(up_token)
    down_book = get_orderbook(down_token)
    
    up_bid, up_ask, up_mid = get_best_prices(up_book)
    down_bid, down_ask, down_mid = get_best_prices(down_book)
    
    if None in (up_ask, down_ask):
        return
    
    obi_up = calculate_obi(up_book)
    
    # Fair Value
    time_fraction = max(seconds_left / 300, 0.05)
    z_score = move / (current_price * volatility * (time_fraction ** 0.5) + 1e-9)
    
    logit = 1.15 * z_score + 0.9 * obi_up
    p_fair_up = 1 / (1 + math.exp(-logit))
    p_fair_up = max(0.05, min(0.95, p_fair_up))
    
    edge_up = p_fair_up - up_ask
    edge_down = (1 - p_fair_up) - down_ask
    
    confidence = min(99, int(50 + abs(z_score)*18 + abs(obi_up)*25))
    
    # Сигнал
    signal = "НЕМАЄ СИГНАЛУ"
    side = None
    edge = 0.0
    
    if seconds_left > (300 - EARLY_SECONDS):
        signal = "ЗАРАНО (перші 60 сек)"
    elif edge_up >= MIN_EDGE and confidence >= MIN_CONFIDENCE:
        signal = "BUY UP"
        side = "UP"
        edge = edge_up
    elif edge_down >= MIN_EDGE and confidence >= MIN_CONFIDENCE:
        signal = "BUY DOWN"
        side = "DOWN"
        edge = edge_down
    
    # Зберігаємо новий сигнал (тільки один на вікно)
    if side:
        already_signaled = any(s["window_start"] == window_start for s in stats["signals"])
        if not already_signaled:
            window_str = f"{datetime.fromtimestamp(window_start, tz=timezone.utc).strftime('%H:%M')}–{datetime.fromtimestamp(window_end, tz=timezone.utc).strftime('%H:%M')}"
            stats["signals"].append({
                "window_start": window_start,
                "window_end": window_end,
                "window_str": window_str,
                "side": side,
                "edge": round(edge, 4),
                "confidence": confidence,
                "resolved": False,
                "timestamp": int(time.time())
            })
            save_stats(stats)
    
    # Статистика
    winrate = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
    streak = 0
    for s in reversed(stats["signals"]):
        if not s.get("resolved"):
            continue
        if s.get("win"):
            if streak >= 0:
                streak += 1
            else:
                break
        else:
            if streak <= 0:
                streak -= 1
            else:
                break
    
    streak_text = f"+{streak}" if streak > 0 else str(streak) if streak < 0 else "0"
    
    # Повідомлення
    mins, secs = divmod(seconds_left, 60)
    
    msg = f"""
<b>ETH 5m Pro Basic</b>
Вікно: {datetime.fromtimestamp(window_start, tz=timezone.utc).strftime('%H:%M')}–{datetime.fromtimestamp(window_end, tz=timezone.utc).strftime('%H:%M')} UTC
Залишилось: <b>{mins}:{secs:02d}</b>

Price to Beat: <code>{ptb:.2f}</code>
Ціна зараз: <code>{current_price:.2f}</code> ({move:+.2f})
z-score: <b>{z_score:+.2f}</b>

Up: {up_bid:.2f}/{up_ask:.2f} | Down: {down_bid:.2f}/{down_ask:.2f}
OBI: {obi_up:+.2f} | Fair P(Up): <b>{p_fair_up:.1%}</b>

Edge Up: {edge_up:+.1%} | Edge Down: {edge_down:+.1%}

<b>СИГНАЛ: {signal}</b>
Впевненість: {confidence}/100
"""
    if side:
        msg += f"Рекомендація: <b>{side}</b> (edge {edge:.1%})\n"
    
    msg += f"""
──────────────
Статистика сигналів:
Всього: {stats['total']} | Перемоги: {stats['wins']} | Поразки: {stats['losses']}
Вінрейт: <b>{winrate:.1f}%</b> | Серія: {streak_text}
"""
    
    send_telegram(msg.strip())
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {signal} | Winrate {winrate:.1f}%")

# ====================== ЗАПУСК ======================
print("Бот Pro Basic + Статистика запущено...")
send_telegram("ETH 5m Pro Basic + Статистика запущено")

schedule.every(40).seconds.do(analyze)

analyze()  # перший запуск одразу

while True:
    schedule.run_pending()
    time.sleep(1)
