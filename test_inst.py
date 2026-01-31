
import asyncio
from pyquotex.stable_api import Quotex
from pyquotex.config import credentials

async def test():
    email, password = credentials()
    q = Quotex(email=email, password=password)
    print("Connecting...")
    await q.connect()
    print("Fetching instruments...")
    inst = await q.get_instruments()
    print(f"Total instruments: {len(inst)}")
    if inst:
        print(f"First instrument: {inst[0]}")
    await q.close()

if __name__ == "__main__":
    asyncio.run(test())
