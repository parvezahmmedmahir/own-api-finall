
import asyncio
import json
import os
import time
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pyquotex.stable_api import Quotex
from pyquotex.config import credentials, load_session
from datetime import datetime

app = FastAPI(title="PyQuotex Headless API")

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Quotex client
client = None

async def get_client():
    global client
    if client is None:
        print("Initializing Quotex client...")
        try:
            email, password = credentials()
            client = Quotex(email=email, password=password)
            check, reason = await client.connect()
            if not check:
                print(f"Connection failed: {reason}")
                client = None
                return None
            print("Quotex client initialized.")
        except Exception as e:
            print(f"Error initializing client: {e}")
            client = None
            return None
    return client

@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "PyQuotex Headless API is running",
        "endpoints": {
            "ws": "/ws (WebSocket for live data)",
            "assets": "/api/assets",
            "balance": "/api/balance",
            "profile": "/api/profile"
        }
    }

@app.get("/api/assets")
async def get_assets():
    q_client = await get_client()
    if not q_client:
        return {"error": "API not connected"}
    
    instruments = await q_client.get_instruments()
    asset_list = []
    for i in instruments:
        try:
            if len(i) > 14:
                asset_list.append({
                    "id": i[0], 
                    "symbol": i[1], 
                    "name": i[2],
                    "open": bool(i[14])
                })
        except:
            continue
    return asset_list

@app.get("/api/balance")
async def get_balance():
    q_client = await get_client()
    if not q_client:
        return {"error": "API not connected"}
    await q_client.change_account("PRACTICE")
    balance = await q_client.get_balance()
    return {"balance": balance, "currency": "BRL"}

@app.get("/api/profile")
async def get_profile():
    q_client = await get_client()
    if not q_client:
        return {"error": "API not connected"}
    profile = await q_client.get_profile()
    return {
        "nick_name": profile.nick_name,
        "profile_id": profile.profile_id,
        "country": profile.country_name,
        "demo_balance": profile.demo_balance,
        "live_balance": profile.live_balance
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[WS] WebSocket accepted.")
    
    q_client = await get_client()
    if q_client is None:
        await websocket.send_json({"type": "error", "message": "Failed to connect to Quotex"})
        await websocket.close()
        return

    active_asset = None
    active_period = 60
    
    # Send instrument list initially
    instruments = await q_client.get_instruments()
    asset_list = []
    for i in instruments:
        try:
            if len(i) > 14:
                asset_list.append({
                    "id": i[0], 
                    "symbol": i[1], 
                    "name": i[2],
                    "open": bool(i[14])
                })
        except:
            continue
    
    await websocket.send_json({"type": "assets", "data": asset_list})

    async def tick_relay():
        nonlocal active_asset
        try:
            while True:
                if active_asset:
                    ticks = q_client.api.realtime_price.get(active_asset, [])
                    if ticks:
                        q_client.api.realtime_price[active_asset] = []
                        for tick in ticks:
                            await websocket.send_json({
                                "type": "tick",
                                "data": {
                                    "time": tick['time'],
                                    "price": tick['price']
                                }
                            })
                await asyncio.sleep(0.1)
        except Exception:
            pass

    relay_task = asyncio.create_task(tick_relay())

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            if data["type"] == "switch":
                new_asset = data["asset"]
                new_period = int(data.get("period", 60))
                
                if active_asset:
                    q_client.stop_candles_stream(active_asset)
                
                active_asset = new_asset
                active_period = new_period
                
                history = await q_client.get_candles_v3(active_asset, 300, active_period)
                q_client.start_candles_stream(active_asset, active_period)
                
                await websocket.send_json({
                    "type": "history",
                    "data": history
                })
                
    except WebSocketDisconnect:
        relay_task.cancel()
        if active_asset:
            q_client.stop_candles_stream(active_asset)
    except Exception:
        relay_task.cancel()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
