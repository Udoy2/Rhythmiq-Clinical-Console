#!/usr/bin/env python3
"""Minimal stand-in for the backend's receiving side of CONTRACT §2.1 (`WS /v1/stream/{device_id}?key=`).

Validates hello and frames, checks seq / t0 continuity (duplicates dropped, t0 jumps recorded as gaps),
replies ack, prints stats. For testing the senders without the real backend:

    python sender/mock_server.py --port 8000
    python sender/mock_server.py --port 8000 --keys dev-demo-208=demo-key-208   # enforce device keys
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit

import websockets
from websockets.exceptions import ConnectionClosed


class Invalid(Exception):
    pass


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_hello(msg, device: str):
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        raise Invalid("first message must be hello")
    if msg.get("device_id") != device:
        raise Invalid(f"hello.device_id {msg.get('device_id')!r} does not match path {device!r}")
    if not _is_num(msg.get("fs")) or msg["fs"] <= 0:
        raise Invalid("hello.fs must be a positive number")
    if msg.get("source") not in ("serial", "replay"):
        raise Invalid("hello.source must be 'serial' or 'replay'")
    if "adc" in msg:
        adc = msg["adc"]
        if not (isinstance(adc, dict) and _is_num(adc.get("zero")) and _is_num(adc.get("gain")) and adc["gain"] > 0
                and isinstance(adc.get("units"), str)):
            raise Invalid("hello.adc must be {zero:number, gain:number>0, units:str}")
    if not isinstance(msg.get("firmware", ""), str):
        raise Invalid("hello.firmware must be a string")


@dataclass
class Session:
    device: str
    hello: dict
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    frames: int = 0
    samples: int = 0
    duplicates: int = 0
    lead_off_frames: int = 0
    gaps: list = field(default_factory=list)          # [{"at_sample", "missing"}]
    annotations: list = field(default_factory=list)   # [(sample, symbol)]
    errors: list = field(default_factory=list)
    arrivals: list = field(default_factory=list)      # [(monotonic time, end sample index)]
    values: list | None = None                        # all samples, when the server keeps them
    first_t0: int | None = None
    bye: bool = False
    closed: bool = False
    last_seq: int | None = None
    next_t0: int | None = None

    def on_frame(self, msg, keep_samples: bool):
        h = self.hello
        seq, t0, samples = msg.get("seq"), msg.get("t0"), msg.get("samples")
        if not (isinstance(seq, int) and seq >= 0 and isinstance(t0, int) and t0 >= 0):
            raise Invalid("frame.seq and frame.t0 must be non-negative integers")
        if msg.get("device_id") != self.device:
            raise Invalid("frame.device_id does not match the stream")
        if msg.get("fs") != h["fs"] or msg.get("source") != h["source"]:
            raise Invalid("frame.fs/source differ from hello")
        if not (isinstance(samples, list) and samples and all(_is_num(s) for s in samples)):
            raise Invalid("frame.samples must be a non-empty list of numbers")
        if not isinstance(msg.get("lead_off"), bool):
            raise Invalid("frame.lead_off must be a boolean")
        if self.last_seq is not None and seq <= self.last_seq:
            self.duplicates += 1                       # contract: duplicate seq dropped
            return
        self.last_seq = seq
        if self.next_t0 is None:
            self.first_t0 = t0
            if t0:
                self.gaps.append({"at_sample": 0, "missing": t0})
        elif t0 > self.next_t0:
            self.gaps.append({"at_sample": self.next_t0, "missing": t0 - self.next_t0})  # never interpolated
        elif t0 < self.next_t0:
            raise Invalid(f"frame.t0 {t0} overlaps data already received (expected {self.next_t0})")
        end = t0 + len(samples)
        for a in msg.get("annotations") or []:
            if not (isinstance(a, dict) and isinstance(a.get("sample"), int) and isinstance(a.get("symbol"), str)
                    and a["symbol"]):
                raise Invalid("annotation must be {sample:int, symbol:str}")
            if not t0 <= a["sample"] < end:
                raise Invalid(f"annotation sample {a['sample']} outside frame [{t0}, {end})")
            self.annotations.append((a["sample"], a["symbol"]))
        self.next_t0 = end
        self.frames += 1
        self.samples += len(samples)
        self.lead_off_frames += msg["lead_off"]
        self.arrivals.append((time.monotonic(), end))
        if keep_samples:
            self.values = self.values or []
            self.values.extend(samples)


class MockServer:
    """`async with MockServer() as srv:` -> srv.url, srv.sessions. Test hooks: `drop_after` closes a connection
    abruptly after that many frames (popped per connection, so [5] drops only the first one)."""

    def __init__(self, host="127.0.0.1", port=0, keys: dict | None = None, model_loaded=True,
                 keep_samples=False, drop_after: list | None = None, log=None):
        self.host, self.port, self.keys, self.model_loaded = host, port, keys, model_loaded
        self.keep_samples, self.drop_after, self.log = keep_samples, list(drop_after or []), log or (lambda m: None)
        self.sessions: list[Session] = []
        self.active: dict[str, object] = {}
        self.rejected = 0

    async def __aenter__(self):
        self._srv = await websockets.serve(self._handle, self.host, self.port, compression=None, max_size=2 ** 20)
        self.port = self._srv.sockets[0].getsockname()[1]
        self.url = f"ws://{self.host}:{self.port}"
        return self

    async def __aexit__(self, *exc):
        self._srv.close()
        await self._srv.wait_closed()

    async def _reject(self, ws, detail: str, sess: Session | None = None):
        self.rejected += 1
        if sess:
            sess.errors.append(detail)
        self.log(f"reject: {detail}")
        await ws.send(json.dumps({"type": "error", "detail": detail}))
        await ws.close(1008, detail[:100])

    async def _handle(self, ws):
        url = urlsplit(ws.request.path)
        parts = url.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["v1", "stream"]:
            return await self._reject(ws, f"unknown path {url.path}")
        device, key = unquote(parts[2]), (parse_qs(url.query).get("key") or [""])[0]
        if not key or (self.keys is not None and self.keys.get(device) != key):
            return await self._reject(ws, "invalid device id or key")
        sess = None
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            check_hello(hello, device)
            if (old := self.active.get(device)) is not None:   # a new connection ends the old session
                await old.close(4001, "superseded by a new connection")
            self.active[device] = ws
            sess = Session(device, hello)
            self.sessions.append(sess)
            limit = self.drop_after.pop(0) if self.drop_after else None
            await ws.send(json.dumps({"type": "ack", "session_id": sess.session_id, "model_loaded": self.model_loaded}))
            self.log(f"{device}: session {sess.session_id} fs={hello['fs']} source={hello['source']} "
                     f"record={hello.get('record')} adc={hello.get('adc')}")
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "frame":
                    sess.on_frame(msg, self.keep_samples)
                    if limit is not None and sess.frames >= limit:
                        ws.transport.abort()               # simulate a network drop (no close handshake)
                        return
                elif kind == "bye":
                    sess.bye = True
                    break
                else:
                    raise Invalid(f"unexpected message type {kind!r}")
        except (Invalid, ValueError) as e:
            await self._reject(ws, str(e), sess)
        except asyncio.TimeoutError:
            await self._reject(ws, "no hello within 10 s")
        except ConnectionClosed:
            pass
        finally:
            if self.active.get(device) is ws:
                del self.active[device]
            if sess:
                sess.closed = True
                self.log(f"{device}: session end frames={sess.frames} samples={sess.samples} gaps={len(sess.gaps)} "
                         f"dups={sess.duplicates} annotations={len(sess.annotations)} bye={sess.bye}")

    def totals(self) -> dict:
        s = self.sessions
        return {"sessions": len(s), "active": len(self.active), "frames": sum(x.frames for x in s),
                "samples": sum(x.samples for x in s), "gaps": sum(len(x.gaps) for x in s),
                "duplicates": sum(x.duplicates for x in s), "annotations": sum(len(x.annotations) for x in s),
                "lead_off_frames": sum(x.lead_off_frames for x in s), "errors": sum(len(x.errors) for x in s),
                "rejected": self.rejected}


async def _serve(args):
    keys = dict(k.split("=", 1) for k in args.keys) if args.keys else None
    async with MockServer(args.host, args.port, keys, model_loaded=not args.no_model,
                          log=lambda m: print(m, flush=True)) as srv:
        print(f"mock CardioSense stream server on {srv.url}/v1/stream/{{device_id}}?key=... "
              f"({'keys enforced' if keys else 'any non-empty key accepted'})", flush=True)
        prev, t_prev = srv.totals(), time.monotonic()
        while True:
            await asyncio.sleep(args.stats_every)
            cur, now = srv.totals(), time.monotonic()
            if cur != prev:
                dt = now - t_prev
                print(f"active {cur['active']} | sessions {cur['sessions']} | frames {cur['frames']} "
                      f"({(cur['frames'] - prev['frames']) / dt:.0f}/s) | samples {cur['samples']} "
                      f"({(cur['samples'] - prev['samples']) / dt:.0f}/s) | gaps {cur['gaps']} | dups {cur['duplicates']} "
                      f"| annotations {cur['annotations']} | lead-off frames {cur['lead_off_frames']} "
                      f"| errors {cur['errors']} | rejected {cur['rejected']}", flush=True)
            prev, t_prev = cur, now


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--keys", nargs="*", metavar="DEVICE=KEY", help="enforce these device keys (default: any key)")
    ap.add_argument("--no-model", action="store_true", help="ack with model_loaded=false (rules-only mode)")
    ap.add_argument("--stats-every", type=float, default=5.0)
    try:
        asyncio.run(_serve(ap.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
