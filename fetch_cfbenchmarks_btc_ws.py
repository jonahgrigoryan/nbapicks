#!/usr/bin/env python3
"""
fetch_cfbenchmarks_btc_ws.py

Streams live BTC price from CF Benchmarks' public websocket (as used by
https://www.cfbenchmarks.com/data/indices/BRTI).

Default stream is BRTI (CME CF Bitcoin Real Time Index), which updates once/sec.

How it works
- Fetches the public index page and extracts Next.js `__NEXT_DATA__` pageProps:
  - socketUrl: websocket URL (e.g. wss://www.cfbenchmarks.com/ws/v4)
  - apiKeyProtocol, wsApiKeyId, wsApiKeyPassword: sent as subprotocols
- Connects via WebSocket using those subprotocols
- Sends: {"type":"subscribe","id":"BRTI","stream":"value"}
- Prints each incoming tick as JSON (one line per message)

Usage
  python3 fetch_cfbenchmarks_btc_ws.py
  python3 fetch_cfbenchmarks_btc_ws.py --index-id BRTI --count 10
  python3 fetch_cfbenchmarks_btc_ws.py --duration 30

Advanced (override autodiscovery)
  CFB_WS_URL=wss://www.cfbenchmarks.com/ws/v4 \
  CFB_WS_PROTOCOL=cfb \
  CFB_WS_KEY_ID=cfbenchmarksws2 \
  CFB_WS_KEY_PASSWORD=... \
  python3 fetch_cfbenchmarks_btc_ws.py --index-id BRTI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
import websockets


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


@dataclass(frozen=True)
class WsConfig:
    url: str
    protocol: str
    key_id: str
    key_password: str

    @property
    def subprotocols(self) -> list[str]:
        # CF Benchmarks uses 3 websocket subprotocol tokens.
        return [self.protocol, self.key_id, self.key_password]


def _utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def discover_ws_config(index_id: str, timeout_s: float = 20.0) -> WsConfig:
    """
    Discover websocket connection params from the public index page.

    CF Benchmarks exposes these in the server-rendered HTML (Next.js pageProps).
    """
    url = f"https://www.cfbenchmarks.com/data/indices/{index_id}"
    resp = requests.get(
        url,
        timeout=timeout_s,
        headers={
            "User-Agent": "cfb-ws-client/1.0 (+https://www.cfbenchmarks.com)",
        },
    )
    resp.raise_for_status()

    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        raise RuntimeError("Could not find __NEXT_DATA__ JSON in page HTML")

    data = json.loads(m.group(1))
    page_props = (data.get("props") or {}).get("pageProps") or {}

    ws_url = str(page_props.get("socketUrl") or "")
    protocol = str(page_props.get("apiKeyProtocol") or "")
    key_id = str(page_props.get("wsApiKeyId") or "")
    key_password = str(page_props.get("wsApiKeyPassword") or "")

    missing = [k for k, v in {
        "socketUrl": ws_url,
        "apiKeyProtocol": protocol,
        "wsApiKeyId": key_id,
        "wsApiKeyPassword": key_password,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Missing websocket fields in pageProps: {missing}")

    return WsConfig(url=ws_url, protocol=protocol, key_id=key_id, key_password=key_password)


def load_ws_config_from_env() -> Optional[WsConfig]:
    ws_url = os.getenv("CFB_WS_URL", "").strip()
    protocol = os.getenv("CFB_WS_PROTOCOL", "").strip()
    key_id = os.getenv("CFB_WS_KEY_ID", "").strip()
    key_password = os.getenv("CFB_WS_KEY_PASSWORD", "").strip()

    if not any([ws_url, protocol, key_id, key_password]):
        return None

    missing = [k for k, v in {
        "CFB_WS_URL": ws_url,
        "CFB_WS_PROTOCOL": protocol,
        "CFB_WS_KEY_ID": key_id,
        "CFB_WS_KEY_PASSWORD": key_password,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Some CFB websocket env vars are set, but missing: {missing}")

    return WsConfig(url=ws_url, protocol=protocol, key_id=key_id, key_password=key_password)


async def stream_index_values(
    index_id: str,
    cfg: WsConfig,
    *,
    stream: str = "value",
    count: Optional[int] = None,
    duration_s: Optional[float] = None,
    quiet: bool = False,
) -> int:
    """
    Connects, subscribes, and prints messages. Returns number of printed ticks.
    """
    subscribe_msg = {"type": "subscribe", "id": index_id, "stream": stream}
    deadline = time.monotonic() + duration_s if duration_s is not None else None

    printed = 0
    async with websockets.connect(
        cfg.url,
        subprotocols=cfg.subprotocols,
        # Server sends data every second for RTIs; keepalive is just belt-and-braces.
        ping_interval=25,
        ping_timeout=25,
        close_timeout=10,
    ) as ws:
        await ws.send(json.dumps(subscribe_msg))

        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            if count is not None and printed >= count:
                break

            try:
                # If duration is set, cap wait so we can stop promptly.
                timeout = None
                if deadline is not None:
                    timeout = max(0.1, min(5.0, deadline - time.monotonic()))

                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                if not quiet:
                    print(raw, flush=True)
                continue

            if msg.get("type") != stream or msg.get("id") != index_id:
                # Ignore unrelated messages.
                continue

            # Normalize output.
            out: Dict[str, Any] = {
                "source": "cfbenchmarks",
                "index_id": index_id,
                "type": msg.get("type"),
                "time_ms": msg.get("time"),
                "time_utc": _utc_iso(int(msg["time"])) if "time" in msg else None,
                "value": msg.get("value"),
                "raw": msg,
            }

            print(json.dumps(out, separators=(",", ":")), flush=True)
            printed += 1

    return printed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream live BTC price from CF Benchmarks websocket (default: BRTI)."
    )
    parser.add_argument(
        "--index-id",
        default="BRTI",
        help="CF Benchmarks external index ID to subscribe to (default: BRTI).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Stop after N ticks (default: unlimited).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Stop after N seconds (default: unlimited).",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Disable autodiscovery; require CFB_WS_* env vars to be set.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-JSON messages (rare).",
    )
    args = parser.parse_args()

    if args.count is not None and args.count <= 0:
        print("Error: --count must be > 0", file=sys.stderr)
        sys.exit(2)
    if args.duration is not None and args.duration <= 0:
        print("Error: --duration must be > 0", file=sys.stderr)
        sys.exit(2)

    try:
        cfg = load_ws_config_from_env()
        if cfg is None:
            if args.no_discover:
                raise RuntimeError("No CFB_WS_* env vars set and --no-discover was provided.")
            cfg = discover_ws_config(args.index_id)
    except Exception as e:
        print(f"[ERROR] Failed to get websocket config: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        printed = asyncio.run(
            stream_index_values(
                args.index_id,
                cfg,
                count=args.count,
                duration_s=args.duration,
                quiet=args.quiet,
            )
        )
    except KeyboardInterrupt:
        printed = 0
    except Exception as e:
        print(f"[ERROR] Websocket streaming failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.count is not None or args.duration is not None:
        print(f"[INFO] streamed {printed} ticks", file=sys.stderr)


if __name__ == "__main__":
    main()

