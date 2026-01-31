
import asyncio
import json
from pyquotex.stable_api import Quotex
from pyquotex.config import credentials

async def debug_candles():
    email, password = credentials()
    client = Quotex(email=email, password=password)
    await client.connect()
    
    asset = "ADAUSD_otc"
    period = 60
    print(f"Fetching candles for {asset}...")
    history = await client.get_candles_v3(asset, 5, period)
    
    print("\nFETCHED HISTORY:")
    print(json.dumps(history, indent=2))
    
    await client.close()

if __name__ == "__main__":
    asyncio.run(debug_candles())
