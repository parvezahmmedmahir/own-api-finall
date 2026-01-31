import os
import json
import asyncio
import time
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pyquotex.stable_api import Quotex
from pyquotex.config import credentials
from datetime import datetime, timedelta

app = FastAPI(title="PyQuotex Headless API + Data Collector")

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Quotex client and connection status
client = None
last_error = "Not initialized"

# Data Directory Setup
DATA_DIR = "market_data"
os.makedirs(f"{DATA_DIR}/24h", exist_ok=True)
os.makedirs(f"{DATA_DIR}/7d", exist_ok=True)

async def get_client():
    global client, last_error
    if client is None:
        try:
            email, password = credentials()
            if not email or not password:
                last_error = "Missing QUOTEX_EMAIL or QUOTEX_PASSWORD"
                return None
            
            client = Quotex(email=email, password=password)
            check, reason = await client.connect()
            if not check:
                last_error = f"Connection failed: {reason}"
                if "pin" in str(reason).lower() or "verify" in str(reason).lower():
                    return client
                client = None
                return None
            last_error = "Connected"
        except Exception as e:
            last_error = f"Exception: {str(e)}"
            client = None
    return client

# Helper to manage JSON data
def save_assets_data(folder, asset, candle):
    file_path = f"{DATA_DIR}/{folder}/{asset}.json"
    data = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
        except: data = []
    
    if data and data[-1]['time'] == candle['time']:
        return

    data.append(candle)
    
    max_size = 20 * 1024 * 1024 if folder == "24h" else 50 * 1024 * 1024
    if os.path.exists(file_path) and os.path.getsize(file_path) > max_size:
        data = data[len(data)//2:]

    with open(file_path, 'w') as f:
        json.dump(data, f)

async def candle_collector_loop():
    global client
    last_rotate_24h = datetime.now()
    last_rotate_7d = datetime.now()

    while True:
        try:
            q_client = await get_client()
            if q_client and last_error == "Connected":
                now = datetime.now()
                # Rotation logic
                if now - last_rotate_24h > timedelta(days=1):
                    for f in os.listdir(f"{DATA_DIR}/24h"): os.remove(f"{DATA_DIR}/24h/{f}")
                    last_rotate_24h = now
                if now - last_rotate_7d > timedelta(days=7):
                    for f in os.listdir(f"{DATA_DIR}/7d"): os.remove(f"{DATA_DIR}/7d/{f}")
                    last_rotate_7d = now

                instruments = await q_client.get_instruments()
                open_assets = [i[1] for i in instruments if len(i) > 14 and i[14]]

                for asset in open_assets[:30]: # Limit to 30 assets
                    try:
                        candles = await q_client.get_candles_v3(asset, 1, 60)
                        if candles:
                            latest = candles[-1]
                            save_assets_data("24h", asset, latest)
                            save_assets_data("7d", asset, latest)
                    except: continue
                    await asyncio.sleep(0.5)

            await asyncio.sleep(60)
        except Exception as e:
            print(f"Collector Error: {e}")
            await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(candle_collector_loop())

@app.get("/")
async def root():
    return {
        "status": "online",
        "connection": last_error,
        "endpoints": {
            "assets": "/api/assets",
            "balance": "/api/balance",
            "history_24h": "/api/history/24h/{asset}",
            "history_7d": "/api/history/7d/{asset}",
            "verify": "/api/verify?pin=XXXXXX"
        }
    }

@app.get("/api/verify")
async def verify_pin(pin: str):
    global client, last_error
    if not client: return {"error": "Client not initialized"}
    check, reason = await client.send_pin(pin)
    if check:
        last_error = "Connected"
        return {"status": "success"}
    return {"status": "failed", "reason": reason}

@app.get("/api/assets")
async def get_assets():
    q_client = await get_client()
    if not q_client or last_error != "Connected":
        return {"error": "API not connected", "reason": last_error}
    instruments = await q_client.get_instruments()
    return [{"symbol": i[1], "name": i[2], "open": bool(i[14])} for i in instruments if len(i) > 14]

@app.get("/api/balance")
async def get_balance():
    q_client = await get_client()
    if not q_client: return {"error": "API not connected"}
    await q_client.change_account("PRACTICE")
    return {"balance": await q_client.get_balance()}

@app.get("/api/history/{timeframe}/{asset}")
async def get_stored_history(timeframe: str, asset: str):
    if timeframe not in ["24h", "7d"]: return {"error": "Invalid timeframe"}
    file_path = f"{DATA_DIR}/{timeframe}/{asset}.json"
    if os.path.exists(file_path):
        with open(file_path, 'r') as f: return json.load(f)
    return {"error": "No data"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    q_client = await get_client()
    if not q_client:
        await websocket.close()
        return

    active_asset = None
    
    async def tick_relay():
        nonlocal active_asset
        try:
            while True:
                if active_asset:
                    ticks = q_client.api.realtime_price.get(active_asset, [])
                    if ticks:
                        q_client.api.realtime_price[active_asset] = []
                        for tick in ticks:
                            await websocket.send_json({"type": "tick", "data": tick})
                await asyncio.sleep(0.1)
        except: pass

    relay_task = asyncio.create_task(tick_relay())

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            if data["type"] == "switch":
                new_asset = data["asset"]
                if active_asset: q_client.stop_candles_stream(active_asset)
                active_asset = new_asset
                history = await q_client.get_candles_v3(active_asset, 300, 60)
                q_client.start_candles_stream(active_asset, 60)
                await websocket.send_json({"type": "history", "data": history})
    except:
        relay_task.cancel()
        if active_asset: q_client.stop_candles_stream(active_asset)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
