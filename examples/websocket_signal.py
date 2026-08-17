#!/usr/bin/env python3
"""Broadcast synthetic signal values to any connected WebSocket client.

This drives ScriptRun.step() from an asyncio event loop instead of
run_realtime()'s blocking thread - asyncio already owns a clock and pumps
callbacks on its own, so it's a second "Layer 2" host for the exact same
step()/hz contract that a real rclpy.Timer adapter would use. Nothing in
signallang itself changes between the two.

Needs the `websockets` package (not a signallang dependency - only for
this example):
    pip install websockets

Run it, then from a browser console:
    python3 examples/websocket_signal.py
    > const ws = new WebSocket("ws://localhost:8765");
    > ws.onmessage = (e) => console.log(e.data);
"""
import asyncio
import json

import websockets

from signallang import compile_script

SCRIPT = """
repeat {
    linear.x = linear!(0, 1.0, 3s);
    send hz 10 dur 3s;
    linear.x = linear!(1.0, 0, 3s);
    send hz 10 dur 3s;
}
"""

clients = set()


async def handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)


async def drive_signal():
    run = compile_script(SCRIPT).new_run()
    while (result := run.step()) is not None:
        if clients:
            payload = json.dumps(result.value)
            await asyncio.gather(*(ws.send(payload) for ws in clients))
        await asyncio.sleep(1.0 / result.hz)


async def main():
    async with websockets.serve(handler, "localhost", 8765):
        print("listening on ws://localhost:8765")
        await drive_signal()


if __name__ == "__main__":
    asyncio.run(main())
