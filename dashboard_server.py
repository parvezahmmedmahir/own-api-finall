import os
import json
import asyncio
import time
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pyquotex.stable_api import Quotex
from pyquotex.config import credentials
from datetime import datetime, timedelta

app = FastAPI(title="PyQuotex Ultra-Live Data Collector")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
client = None
last_error = "Not initialized"
DATA_DIR = "market_data"
os.makedirs(f"{DATA_DIR}/24h", exist_ok=True)
os.makedirs(f"{DATA_DIR}/7d", exist_ok=True)

async def get_client():
    global client, last_error
    if client is None:
        try:
            email, password = credentials()
            if not email or not password:
                last_error = "Missing Credentials"
                return None
            client = Quotex(email=email, password=password)
            check, reason = await client.connect()
            if not check:
                last_error = f"Login Failed: {reason}"
                return client if "pin" in str(reason).lower() else None
            last_error = "Connected"
        except Exception as e:
            last_error = f"Fatal Error: {str(e)}"
    return client

def save_candle(folder, asset, candle):
    """High-speed storage of OHLC data."""
    file_path = f"{DATA_DIR}/{folder}/{asset}.json"
    data = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
        except: data = []
    
    # Avoid duplicates (check timestamp)
    if data and data[-1]['time'] == candle['time']:
        # Update the existing candle (it might have updated High/Low/Close since last tick)
        data[-1] = candle
    else:
        data.append(candle)
    
    # Enforce Limits: 20MB for 24h, 50MB for 7d
    max_size = 20 * 1024 * 1024 if folder == "24h" else 50 * 1024 * 1024
    if os.path.exists(file_path) and os.path.getsize(file_path) > max_size:
        data = data[-(len(data)//2):] # Keep only the most recent half

    with open(file_path, 'w') as f:
        json.dump(data, f)

async def process_asset_batch(q_client, assets):
    """Fetches candle data in parallel batches."""
    tasks = []
    for asset in assets:
        # Fetch last 2 candles (closed one + live one)
        tasks.append(q_client.get_candles_v3(asset, 2, 60))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for i, candles in enumerate(results):
        if candles and not isinstance(candles, Exception):
            asset_name = assets[i]
            for candle in candles:
                save_candle("24h", asset_name, candle)
                save_candle("7d", asset_name, candle)

async def ultra_collector_loop():
    """Main high-speed collection engine."""
    global client
    last_rotate_24h = datetime.now()
    last_rotate_7d = datetime.now()

    while True:
        try:
            # Sync to the next 5-second interval for near real-time updates
            # This is much faster than waiting 60 seconds
            await asyncio.sleep(5) 
            
            q_client = await get_client()
            if q_client and last_error == "Connected":
                now = datetime.now()
                
                # Cleanup/Rotation
                if now - last_rotate_24h > timedelta(days=1):
                    for f in os.listdir(f"{DATA_DIR}/24h"): os.remove(f"{DATA_DIR}/24h/{f}")
                    last_rotate_24h = now
                if now - last_rotate_7d > timedelta(days=7):
                    for f in os.listdir(f"{DATA_DIR}/7d"): os.remove(f"{DATA_DIR}/7d/{f}")
                    last_rotate_7d = now

                # Get ALL open assets
                instruments = await q_client.get_instruments()
                all_open = [i[1] for i in instruments if len(i) > 14 and i[14]]
                
                # Process in batches of 10 for speed
                for i in range(0, len(all_open), 10):
                    batch = all_open[i:i+10]
                    await process_asset_batch(q_client, batch)
                    await asyncio.sleep(0.2) # Small safety gap

        except Exception as e:
            print(f"ULTRA-COLLECTOR ERROR: {e}")
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ultra_collector_loop())

@app.get("/")
async def root():
    return {
        "status": "online",
        "mode": "Ultra-Collector (All Assets)",
        "connection": last_error,
        "endpoints": {
            "all_assets": "/api/assets",
            "history_1m": "/api/history/24h/{asset}",
            "history_7d": "/api/history/7d/{asset}",
            "custom_tf": "/api/history/7d/{asset}?tf=5 (Supports tf 5, 15, 60)"
        }
    }

@app.get("/api/assets")
async def get_assets():
    q_client = await get_client()
    if not q_client: return {"error": "API Error"}
    instruments = await q_client.get_instruments()
    return [{"symbol": i[1], "name": i[2], "open": bool(i[14])} for i in instruments if len(i) > 14]

@app.get("/api/history/{folder}/{asset}")
async def get_history(folder: str, asset: str, tf: int = 1):
    if folder not in ["24h", "7d"]: return {"error": "Invalid folder"}
    
    file_path = f"{DATA_DIR}/{folder}/{asset}.json"
    if not os.path.exists(file_path): return {"error": "Collecting data, please wait..."}
    
    with open(file_path, 'r') as f:
        m1_data = json.load(f)
        
    if tf == 1: return m1_data
    
    # Aggregate to other timeframes (5m, 15m, etc.)
    aggregated = []
    chunk_size = tf
    for i in range(0, len(m1_data), chunk_size):
        chunk = m1_data[i:i+chunk_size]
        if not chunk: continue
        aggregated.append({
            "time": chunk[0]["time"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"]
        })
    return aggregated

@app.get("/api/verify")
async def verify(pin: str):
    global client
    if not client: return {"error": "Init client first"}
    ok, res = await client.send_pin(pin)
    return {"success": ok, "message": res}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    q_client = await get_client()
    if not q_client: await websocket.close(); return
    
    active_asset = None
    
    async def relay():
        nonlocal active_asset
        while True:
            if active_asset:
                ticks = q_client.api.realtime_price.get(active_asset, [])
                if ticks:
                    q_client.api.realtime_price[active_asset] = []
                    for t in ticks: await websocket.send_json({"type": "tick", "data": t})
            await asyncio.sleep(0.1)

    rt = asyncio.create_task(relay())
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            if data["type"] == "switch":
                if active_asset: q_client.stop_candles_stream(active_asset)
                active_asset = data["asset"]
                q_client.start_candles_stream(active_asset, 60)
    except:
        rt.cancel()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
