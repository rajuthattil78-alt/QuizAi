import os
import json
import time
import re
import logging
import asyncio
import httpx
from groq import AsyncGroq

# Configuration
GROQ_API_KEY = "api"
client = AsyncGroq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile" # Robust model

logger = logging.getLogger(__name__)

# File Paths
STATE_FILE = "ai_state.json"
CRYPTO_CACHE_FILE = "crypto_cache.json"

# Limits
RATE_LIMIT_MESSAGES = 30
RATE_LIMIT_WINDOW = 30 * 60  # 30 minutes in seconds
SUSPENSION_TIME = 30 * 60    # 30 minutes in seconds
MAX_PROMPT_LENGTH = 1000

SYSTEM_PROMPT = """You are an Interactive Crypto Quiz Master and Proactive Tutor. Your PRIMARY goal is to TEST the user's knowledge and engage them in a continuous quiz-like conversation.
You MUST ALWAYS respond with a strict, valid JSON dictionary in the exact format below, with NO extra text outside the JSON.

{"context": "Brief summary of what you understood", "action": "reply|fetch_crypto|clear_memory", "reply": "Your message to the user, or the coin ticker if fetching crypto"}

Tutor & Quiz Guidelines:
1. NEVER just act like a passive assistant. You are a teacher!
2. When answering a question, keep your explanation concise, and then IMMEDIATELY follow up with a challenging quiz question (multiple choice or open-ended) to test their understanding of what you just explained.
3. Always end your response with a question that forces the user to think.
4. If the user answers your quiz question correctly, praise them and ask a harder one. If they are wrong, correct them gently and ask a new related question.
5. Use a little bit of emojis to make it friendly, and use '\\n\\n' for paragraphs to keep it readable. (CRITICAL: Do NOT use literal newlines or unescaped quotes inside the JSON string).

Actions:
- "reply": Use this to provide a standard text answer.
- "fetch_crypto": Use this ONLY if the user asks for the current price or data of a cryptocurrency. Set "reply" to the precise ticker symbol (e.g., BTC, ETH, SOL).
- "clear_memory": Use this if the user says they understand, thanks you, or concludes the topic.
"""

def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_state(user_id):
    state = _load_json(STATE_FILE, {})
    uid_str = str(user_id)
    if uid_str not in state:
        state[uid_str] = {
            "ai_mode": False,
            "history": [],
            "requests": [],
            "suspended_until": 0
        }
    return state, uid_str

def save_state(state):
    _save_json(STATE_FILE, state)

def check_rate_limit(user_id):
    state, uid = get_state(user_id)
    user_data = state[uid]
    
    now = time.time()
    if user_data["suspended_until"] > now:
        return False, int((user_data["suspended_until"] - now) / 60)
        
    # Clean up old requests
    user_data["requests"] = [req for req in user_data["requests"] if now - req < RATE_LIMIT_WINDOW]
    
    if len(user_data["requests"]) >= RATE_LIMIT_MESSAGES:
        user_data["suspended_until"] = now + SUSPENSION_TIME
        save_state(state)
        return False, int(SUSPENSION_TIME / 60)
        
    return True, 0

def add_request(user_id):
    state, uid = get_state(user_id)
    state[uid]["requests"].append(time.time())
    save_state(state)

def toggle_ai_mode(user_id, enable: bool):
    state, uid = get_state(user_id)
    state[uid]["ai_mode"] = enable
    if enable:
        state[uid]["history"] = [{"role": "system", "content": SYSTEM_PROMPT}]
    else:
        state[uid]["history"] = []
    save_state(state)

def is_ai_mode(user_id):
    state, uid = get_state(user_id)
    return state[uid].get("ai_mode", False)

async def _fetch_from_api(ticker):
    ticker = ticker.upper()
    async with httpx.AsyncClient() as client:
        # Binance first
        binance_symbol = f"{ticker}USDT"
        try:
            res = await client.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={binance_symbol}", timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                result = f"{ticker} (Binance): Price ${float(data['lastPrice']):.4f}, 24h Change: {float(data['priceChangePercent']):.2f}%, Vol: {float(data['volume']):.2f}"
                return result
        except Exception as e:
            logger.warning(f"Binance fetch failed for {ticker}: {e}")

        # Fallback to CoinGecko
        try:
            search_res = await client.get(f"https://api.coingecko.com/api/v3/search?query={ticker}", timeout=5.0)
            if search_res.status_code == 200 and search_res.json().get('coins'):
                coin_id = search_res.json()['coins'][0]['id']
                cg_res = await client.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true", timeout=5.0)
                if cg_res.status_code == 200:
                    cg_data = cg_res.json()[coin_id]
                    result = f"{ticker} (CoinGecko): Price ${cg_data['usd']:.4f}, 24h Change: {cg_data.get('usd_24h_change', 0):.2f}%"
                    return result
        except Exception as e:
            logger.warning(f"CoinGecko fetch failed for {ticker}: {e}")
            
    return None

async def fetch_crypto_data(ticker):
    ticker = ticker.upper()
    cache = _load_json(CRYPTO_CACHE_FILE, {})
    now = time.time()
    
    if ticker in cache and now - cache[ticker].get("timestamp", 0) < 900:
        return cache[ticker]["data"]
        
    result = await _fetch_from_api(ticker)
    if result:
        # Reload cache to avoid overwriting worker updates
        cache = _load_json(CRYPTO_CACHE_FILE, {})
        cache[ticker] = {"timestamp": time.time(), "data": result}
        _save_json(CRYPTO_CACHE_FILE, cache)
        return result
            
    return f"Could not fetch data for {ticker} from primary sources."

async def crypto_update_worker():
    logger.info("Crypto cache background worker started.")
    while True:
        try:
            cache = _load_json(CRYPTO_CACHE_FILE, {})
            coins = list(cache.keys())
            
            if not coins:
                await asyncio.sleep(600)  # Wait 10 minutes if no coins tracked
                continue
                
            # Spread updates over 5 minutes (300 seconds)
            sleep_between_coins = 300.0 / len(coins)
            
            for ticker in coins:
                result = await _fetch_from_api(ticker)
                if result:
                    current_cache = _load_json(CRYPTO_CACHE_FILE, {})
                    current_cache[ticker] = {"timestamp": time.time(), "data": result}
                    _save_json(CRYPTO_CACHE_FILE, current_cache)
                    
                await asyncio.sleep(sleep_between_coins)
                
            # Rest phase for 5 minutes
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"Error in crypto_update_worker: {e}")
            await asyncio.sleep(60)

def start_worker():
    asyncio.create_task(crypto_update_worker())

async def process_ai_message(user_id, text, update_status=None):
    if len(text) > MAX_PROMPT_LENGTH:
        return {"action": "reply", "reply": "⚠️ Your prompt is too long. Please keep it under 1000 characters."}
        
    allowed, wait_time = check_rate_limit(user_id)
    if not allowed:
        return {"action": "reply", "reply": f"⏳ You have hit the limit! Please try again after {wait_time} mins... let's learn slowly with a smile 😊"}
        
    add_request(user_id)
    state, uid = get_state(user_id)
    history = state[uid]["history"]
    history.append({"role": "user", "content": text})
    
    # Keep history manageable (last 20 interactions + system prompt)
    if len(history) > 21:
        history = [history[0]] + history[-20:]
        
    try:
        if update_status: await update_status("Confirming...")
        response = await client.chat.completions.create(
            model=MODEL,
            messages=history,
            temperature=0.6,
            max_tokens=800
        )
        ai_output = response.choices[0].message.content
        
        # Regex to extract JSON block
        json_match = re.search(r'(\{.*\})', ai_output, re.DOTALL)
        if not json_match:
            # Fallback
            return {"action": "reply", "reply": ai_output}
            
        try:
            ai_data = json.loads(json_match.group(1), strict=False)
        except json.JSONDecodeError as jde:
            logger.error(f"JSON Parsing failed: {jde}. Raw output: {ai_output}")
            # Fallback to returning the raw text instead of a generic error
            return {"action": "reply", "reply": ai_output}
        
        action = ai_data.get("action", "reply")
        reply_text = ai_data.get("reply", "")
        
        if action == "clear_memory":
            toggle_ai_mode(user_id, True) # resets history
            return {"action": "clear_memory", "reply": reply_text}
            
        if action == "fetch_crypto":
            if update_status: await update_status("Fetching live crypto data...")
            crypto_info = await fetch_crypto_data(reply_text)
            # Feed back to AI to form final answer
            history.append({"role": "assistant", "content": json.dumps(ai_data)})
            history.append({"role": "user", "content": f"Here is the live data: {crypto_info}. Now provide a final cohesive tutor response, and make sure to end by asking a relevant quiz question about this coin or its concepts to test me."})
            
            if update_status: await update_status("Finalizing response...")
            final_response = await client.chat.completions.create(
                model=MODEL,
                messages=history,
                temperature=0.6,
                max_tokens=800
            )
            final_output = final_response.choices[0].message.content
            final_match = re.search(r'(\{.*\})', final_output, re.DOTALL)
            if final_match:
                try:
                    final_data = json.loads(final_match.group(1), strict=False)
                    reply_text = final_data.get("reply", "Here is your data.")
                except json.JSONDecodeError:
                    reply_text = final_output
                history.append({"role": "assistant", "content": final_output})
            else:
                reply_text = final_output
                history.append({"role": "assistant", "content": final_output})
        else:
            history.append({"role": "assistant", "content": json.dumps(ai_data)})
            
        state[uid]["history"] = history
        save_state(state)
        
        return {"action": action, "reply": reply_text}
        
    except Exception as e:
        logger.error(f"Groq API Error: {e}")
        return {"action": "reply", "reply": "⚠️ An error occurred while processing your AI request."}
