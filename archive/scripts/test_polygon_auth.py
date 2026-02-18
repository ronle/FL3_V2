#!/usr/bin/env python3
"""Quick Polygon websocket auth test."""

import asyncio
import json
import os
import websockets

async def test_auth():
    api_key = os.environ.get("POLYGON_API_KEY") or "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
    if not api_key:
        print("POLYGON_API_KEY not set")
        return

    print(f"API Key length: {len(api_key)}")
    print(f"API Key (first 10): {api_key[:10]}...")

    url = "wss://socket.polygon.io/options"
    print(f"\nConnecting to {url}...")

    try:
        async with websockets.connect(url) as ws:
            # Receive connection message
            msg = await ws.recv()
            print(f"Connection response: {msg}")

            # Authenticate
            auth_msg = {"action": "auth", "params": api_key}
            print(f"\nSending auth...")
            await ws.send(json.dumps(auth_msg))

            # Receive auth response
            response = await ws.recv()
            print(f"Auth response: {response}")

            # Try to subscribe
            sub_msg = {"action": "subscribe", "params": "T.*"}
            print(f"\nSending subscribe...")
            await ws.send(json.dumps(sub_msg))

            response = await ws.recv()
            print(f"Subscribe response: {response}")

            # Wait for some messages
            print("\nWaiting for messages (5 seconds)...")
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"Received: {msg[:200]}...")
            except asyncio.TimeoutError:
                print("No messages received (market may be closed)")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_auth())
