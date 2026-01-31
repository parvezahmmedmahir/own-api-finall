
import asyncio
import time
import json
import os
from datetime import datetime
from pyquotex.stable_api import Quotex
from pyquotex.config import credentials

class MasterDataCollector:
    def __init__(self, email, password, timeframe=60, history_count=600):
        self.client = Quotex(email=email, password=password)
        self.timeframe = timeframe
        self.history_count = history_count
        self.markets = {} # {asset_name: [candles]}
        self.is_running = False
        self.update_count = 0

    async def connect(self):
        check, reason = await self.client.connect()
        if not check:
            print(f"Connection failed: {reason}")
            return False
        print("Connected to Quotex successfully.")
        return True

    async def initialize_assets(self):
        print("Fetching open assets...")
        instruments = await self.client.get_instruments()
        open_assets = []
        for i in instruments:
            if len(i) >= 3 and i[2]:
                open_assets.append(i[1])
        
        print(f"Found {len(open_assets)} open assets.")
        return open_assets

    async def load_history(self, asset):
        try:
            # count=600 history load
            candles = await self.client.get_candles_v3(asset, self.history_count, self.timeframe)
            if candles:
                self.markets[asset] = candles
                return True
            else:
                self.markets[asset] = []
                return False
        except Exception as e:
            self.markets[asset] = []
            return False

    async def subscribe_all(self, assets):
        print(f"Subscribing to live updates for {len(assets)} assets...")
        # Small batches to avoid disconnect
        batch_size = 10
        for i in range(0, len(assets), batch_size):
            batch = assets[i:i + batch_size]
            for asset in batch:
                self.client.api.subscribe_realtime_candle(asset, self.timeframe)
            print(f"  Subscribed to batch {i//batch_size + 1}")
            await asyncio.sleep(0.5)

    async def run_live_processor(self):
        print("\n--- Live Data Collector Active ---")
        print(f"Monitoring {len(self.markets)} markets at {self.timeframe}s timeframe.")
        
        while self.is_running:
            updated_this_tick = 0
            for asset, history in self.markets.items():
                ticks = self.client.api.realtime_price.get(asset, [])
                if not ticks:
                    continue
                
                self.client.api.realtime_price[asset] = []
                updated_this_tick += 1
                
                for tick in ticks:
                    ts = tick['time']
                    price = tick['price']
                    candle_start = int(ts // self.timeframe * self.timeframe)
                    
                    if not history or candle_start > history[-1]['time']:
                        new_candle = {
                            'time': candle_start,
                            'open': price,
                            'high': price,
                            'low': price,
                            'close': price
                        }
                        history.append(new_candle)
                        if len(history) > self.history_count:
                            history.pop(0)
                        self.update_count += 1
                    else:
                        current = history[-1]
                        current['close'] = price
                        current['high'] = max(current['high'], price)
                        current['low'] = min(current['low'], price)

            if updated_this_tick > 0:
                print(f"\rCaptured updates for {updated_this_tick} assets. Total new candles: {self.update_count}", end="")

            await asyncio.sleep(0.1)

    async def start(self):
        if not await self.connect():
            return

        open_assets = await self.initialize_assets()
        
        print("\nPhase 1: Fetching 600 Candles History for ALL assets...")
        # Parallel history loading (limited concurrency)
        semaphore = asyncio.Semaphore(5)
        
        async def fetch_with_sem(asset):
            async with semaphore:
                success = await self.load_history(asset)
                if success:
                    print(f"\r  Progress: {len(self.markets)}/{len(open_assets)} loaded", end="")

        tasks = [fetch_with_sem(asset) for asset in open_assets]
        await asyncio.gather(*tasks)
        print(f"\nCompleted history load for {len(self.markets)} assets.")
        
        print("\nPhase 2: Live Subscription")
        await self.subscribe_all(open_assets)
        
        self.is_running = True
        try:
            await self.run_live_processor()
        except KeyboardInterrupt:
            self.is_running = False
            print("\nShutting down collector...")
        finally:
            await self.client.close()

if __name__ == "__main__":
    email, password = credentials()
    # 60s timeframe as requested for standard analysis
    collector = MasterDataCollector(email, password, timeframe=60, history_count=600)
    asyncio.run(collector.start())
