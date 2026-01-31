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

app = FastAPI(title="PyQuotex Live 600-Candle Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Storage
client = None
last_error = "Not initialized"
# live_buffers[asset] = [ {time, open, high, low, close, ticks}, ... ] (exactly 600)
live_buffers = {}

async def get_client():
    global client, last_error
    if client is None:
        try:
            email, password = credentials()
            if not email or not password:
                last_error = "Missing QUOTEX_EMAIL or QUOTEX_PASSWORD"
                return None
            
            client = Quotex(email=email, password=password)
            try:
                check, reason = await client.connect()
                if not check:
                    last_error = f"Login Failed: {reason}"
                    return client if "pin" in str(reason).lower() else None
                last_error = "Connected"
            except RuntimeError as re:
                # Handle the 'No response stored' error gracefully
                if "No response stored" in str(re):
                    last_error = "Blocked by Quotex (Security). Check Render logs."
                else:
                    last_error = f"Runtime Error: {str(re)}"
                client = None
        except Exception as e:
            last_error = f"Init Error: {str(e)}"
            client = None
    return client

async def update_live_buffer(q_client, asset):
    """Maintains a rolling window of exactly 600 live candles."""
    global live_buffers
    try:
        # Fetch the latest state (includes the running candle)
        # We fetch 601 to ensure we have a full 600 history + 1 running
        candles = await q_client.get_candles_v3(asset, 601, 60)
        if candles:
            # We take the most recent 600. The last one is the 'Open' one.
            live_buffers[asset] = candles[-600:]
            return True
    except:
        return False

async def ultra_live_collector():
    """Engine that refreshes all assets every 2 seconds for live movement."""
    global client, live_buffers
    while True:
        try:
            q_client = await get_client()
            if q_client and last_error == "Connected":
                instruments = await q_client.get_instruments()
                all_open = [i[1] for i in instruments if len(i) > 14 and i[14]]
                
                # Check 15 assets at a time to stay fast
                for i in range(0, len(all_open), 15):
                    batch = all_open[i:i+15]
                    tasks = [update_live_buffer(q_client, asset) for asset in batch]
                    await asyncio.gather(*tasks)
                    await asyncio.sleep(0.1)
                
                # Pause short to sync movement
                await asyncio.sleep(2) 
            else:
                await asyncio.sleep(10)
        except Exception as e:
            print(f"COLLECTOR ERROR: {e}")
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ultra_live_collector())

@app.get("/")
async def root():
    return {
        "status": "online",
        "connection": last_error,
        "mode": "Live 600-Candle Window",
        "total_active_assets": len(live_buffers),
        "endpoints": {
            "live_candles": "/api/live/{asset}",
            "all_assets": "/api/assets",
            "verify": "/api/verify?pin=XXXXXX"
        }
    }

@app.get("/api/live/{asset}")
async def get_live_candles(asset: str):
    """Returns the exactly 600-candle live list for the asset."""
    if asset in live_buffers:
        return live_buffers[asset]
    return {"error": "Data still loading for this asset or asset not found", "active": asset}

@app.get("/api/assets")
async def get_assets():
    q_client = await get_client()
    if not q_client: return {"error": "Disconnected", "reason": last_error}
    instruments = await q_client.get_instruments()
    asset_list = []
    for i in instruments:
        if len(i) > 14:
            asset_list.append({
                "symbol": i[1], 
                "name": i[2], 
                "open": bool(i[14]),
                "collected": i[1] in live_buffers
            })
    return asset_list

@app.get("/api/verify")
async def verify_pin(pin: str):
    global client
    if not client: return {"error": "Client not ready"}
    ok, res = await client.send_pin(pin)
    return {"success": ok, "message": res}

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    q_client = await get_client()
    if not q_client: await websocket.close(); return
    
    active = None
    async def stream():
        while True:
            if active and active in live_buffers:
                # Send the very last (live) candle from our 600 buffer
                await websocket.send_json({"type": "live", "data": live_buffers[active][-1]})
            await asyncio.sleep(1)
            
    st = asyncio.create_task(stream())
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            if data["type"] == "switch": active = data["asset"]
    except: st.cancel()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
