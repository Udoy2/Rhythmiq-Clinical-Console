# CardioSense sender

Device side of `docs/CONTRACT.md` §2.1: turns an ECG source into WebSocket frames for
`WS /v1/stream/{device_id}?key=<device_key>`. Standalone — needs only `websockets`, `numpy` and (hardware
path) `pyserial`; nothing is imported from the backend.

| File | What it is |
|---|---|
| `stream_sender.py` | **ReplaySource**: streams a patient recording (text file) at real-time or accelerated speed |
| `serial_bridge.py` | **SerialSource**: Arduino + AD8232 kit over USB serial → the same frames |
| `multi_sender.py` | N replay streams at once to distinct devices (50-stream soak) |
| `mock_server.py` | Minimal receiving side of §2.1 for testing without the backend |
| `cs_stream.py` | Shared WebSocket client (hello/ack/frames/bye, reconnect), pacing, status line |
| `firmware/cardiosense_ad8232.ino` | Kit firmware: timer-driven 250 Hz sampling, lead-off, version banner |
| `tests/` | pytest suite (formats, annotation alignment, pacing, reconnect, fault injection, serial) |

Python: `~/.venvs/cardiosense/bin/python` (never create a venv inside the repo).

## Replay a recording

```bash
# canonical command (this is what the backend's POST /v1/demo/replay spawns)
python sender/stream_sender.py --file data/records/208.csv --device dev-demo-208 --key demo-key-208 \
    --url ws://localhost:8000 --speed 1

# fast demo: 10x, two minutes starting at 5:00, repeat forever
python sender/stream_sender.py --file data/records/223.csv --device dev-demo-223 --key demo-key-223 \
    --speed 10 --start 300 --duration 120 --loop

# as fast as the server accepts (back-pressure), e.g. to back-fill a session or load-test
python sender/stream_sender.py --file data/records/100.csv --device dev-demo-100 --key demo-key-100 --speed 0

# demo quality gating and gap handling: electrodes off at 0:20 for 8 s, 3 s of lost samples at 0:45,
# heavy noise (0 dB SNR) between 1:00 and 1:15
python sender/stream_sender.py --file data/records/100.csv --device dev-demo-100 --key demo-key-100 \
    --lead-off-at 20:8 --dropout-at 45:3 --noise-db 0 --noise-at 60:15

# the prototype's patient file (one `HR,HRV,ECG` line per sample, 250 lines/s)
python sender/stream_sender.py --file patient.txt --fs 250 --device dev-kit-01 --key <key>
```

What it does: `hello` → waits for `ack` (prints the session id and `model_loaded`) → `frame` every
`--frame-ms` (default 100 ms; 36 samples at 360 Hz) with increasing `seq`, `t0` = sample index since the
start of the stream, and the ground-truth `annotations` whose beats fall in the frame → `bye`.

| Option | Default | Meaning |
|---|---|---|
| `--file PATH` | required | recording; format auto-detected (below) |
| `--device`, `--key` | required | paired device id and device key |
| `--url` | `ws://localhost:8000` | backend base URL (`http(s)://` is accepted too) |
| `--fs HZ` | 360 for MIT-BIH CSV | required for every other format (device range 60–250 Hz) |
| `--lead NAME` | `MLII` | signal column of a MIT-BIH CSV (falls back to the first lead if the record has no MLII) |
| `--column NAME\|INDEX` | last column | signal column of a generic CSV (0-based index or header name) |
| `--speed X` | `1.0` | 1 = real time, 10 = 10×, 0 = unpaced |
| `--start S`, `--duration S` | 0, to end | window of the file, in seconds of signal |
| `--loop` | off | wrap around at end of file (`t0` keeps counting; annotations repeat) |
| `--frame-ms MS` | 100 | frame length |
| `--annotations PATH` | sibling `<id>annotations.txt` | ground-truth file |
| `--no-annotations` / `--all-annotations` | beats only | send no truth / also non-beat markers (`+ ~ \|` …) |
| `--adc-zero`, `--adc-gain`, `--mv` | per format | `mV = (sample − zero) / gain`; `--mv` = samples already in mV (no `adc` in hello) |
| `--record`, `--dataset` | file stem, `MIT-BIH` for MIT-BIH CSV | labels in hello |
| `--lead-off-at T:DUR` | – | fault: `lead_off=true`, signal flat at the baseline, no truth sent; repeatable |
| `--dropout-at T:DUR` | – | fault: samples are not sent and `t0` jumps → the server records a gap; repeatable |
| `--noise-db SNR` (+ `--noise-at T:DUR`, `--seed`) | – | fault: white noise at that SNR (dB, relative to the first 10 s) over the whole stream or the given spans |
| `--max-retries N` | 8 | consecutive failed connects before giving up (0 = forever) |
| `--quiet` | off | errors only |

Fault times are seconds of stream time (0 = first sample sent) and are applied per frame (100 ms granularity).

**Pacing.** Every frame has a deadline `anchor + last_sample_index / (fs × speed)` on the monotonic clock, so
sleep overshoot never accumulates (no drift over 30 min) and after a stall the overdue frames go out at once
until the stream has caught up. Measured against the mock server: 360.0–360.4 samples/s at 1× and 7199.7–7199.9 at 20×
over 20 s (error ≤ 0.11 %; ±2 % is the test limit).

**Reconnect.** If the connection drops, the sender reconnects with exponential backoff (0.5 s → 10 s, jittered),
sends a new `hello` and says so. One connection is one server session, so `t0` restarts at 0 in the new
session while `seq` keeps increasing; replay resumes with the frame that failed (frames in flight when the
link died — typically 0–2 — are not repeated). A rejection (bad key, no consent, error message from the
server, close code 1008/4xxx) is fatal and is not retried.

**Exit codes.** `0` finished · `1` bad input file/options · `2` usage · `3` rejected by the server ·
`4` server unreachable after `--max-retries` · `130` Ctrl-C · `143` SIGTERM (both send `bye` first).

**Output.** On a terminal: a single status line, e.g.
`[dev-demo-208] 00:01:23.4 signal | 834 frames | 360.0 sps (1.00x) | reconnects 0`. When piped (as when the
backend spawns it) the status line is printed every 30 s; connect/reconnect/summary lines are always printed.

## File formats

| Format | Detected by | Signal | Defaults |
|---|---|---|---|
| MIT-BIH CSV export | header whose first column is `sample #`, e.g. `'sample #','MLII','V1'` | `--lead` column | 360 Hz, zero 1024, gain 200 adu/mV, `dataset: "MIT-BIH"`, truth from `<id>annotations.txt` |
| Legacy prototype log | no header, 3 numeric fields `HR,HRV,ECG` | last field | `--fs` required; 10-bit kit ADC: zero 512, gain 225 (nominal) |
| Single column | no header, one number per line | that number | `--fs` required; integers → kit ADC (512 / 225), decimals → already mV |
| Generic CSV | anything else (`,` `;` tab or spaces, header optional) | `--column` | as single column |

Annotation files are `Time  Sample#  Type  Sub Chan Num [Aux]` rows; only `Sample#` and `Type` are used. `Type`
is sent unchanged as the raw MIT-BIH symbol (the server maps it to N/S/V/F). Non-beat markers (rhythm change
`+`, noise `~`, …) are not sent unless `--all-annotations`. Blank or unparseable lines in a recording are
skipped and counted. Files are read lazily, frame by frame — the first frame leaves within milliseconds.

### Adding a new patient file

1. MIT-BIH record: `python scripts/fetch_records.py 201 210` writes `data/records/<id>.csv` and
   `<id>annotations.txt` from PhysioNet in exactly the team's format (byte-identical for the existing records;
   existing files are never overwritten). Or copy the team's export pair into `data/records/`.
2. Any other recording: save it as text, one sample per line (or a CSV), and pass `--fs` plus, when the values
   are ADC counts, `--adc-zero/--adc-gain`. To get truth labels, put a `<name>annotations.txt` next to it.
3. Pair a device for the patient (`POST /v1/devices/pair`) and use its `--device/--key`; the seeded demo
   devices are `dev-demo-<record>` / `demo-key-<record>` for records 100, 223 and 208.

## Hardware path (Arduino + AD8232)

Wiring (SparkFun AD8232 breakout → Uno/Nano):

| AD8232 | Arduino | |
|---|---|---|
| 3.3V | 3.3V | |
| GND | GND | |
| OUTPUT | A0 | 10-bit ADC |
| LO+ | D10 | lead-off detect |
| LO− | D11 | lead-off detect |

Electrodes RA / LA / RL in a lead II placement. Power the board from a battery or a laptop running on
battery — no mains path to the person.

Flash `firmware/cardiosense_ad8232.ino` (Arduino IDE, board Uno or Nano), then:

```bash
python sender/serial_bridge.py --port /dev/ttyUSB0 --device <device id> --key <device key> --url ws://localhost:8000
```

The firmware samples from a Timer1 interrupt at exactly 250 Hz (no `delay()`), prints
`# cardiosense-ad8232 fw=1.0.0 fs=250 adc_bits=10` on connect (and when sent `?`), then one line per sample
at 115200 baud: the raw ADC value, or `!` while LO+ or LO− is high. The bridge also understands the
prototype's `HR,HRV,ECG` lines and the SparkFun example sketch, so older firmware keeps working.

The bridge batches 100 ms frames (25 samples) with `source: "serial"`, `lead_off` true if any sample in the
frame was flagged (the last good value is held for `!` slots), and `t0` from the sample counter. It
- measures the real sample rate every 10 s and warns if it is more than 2 % off `--fs` (the usual cause is
  an old `delay()`-based sketch — reflash, or pass the true `--fs`);
- reopens the serial port with backoff after an unplug and advances `t0` by the samples that were missed,
  so the server records a gap instead of the trace silently closing up;
- reconnects the WebSocket with backoff (new hello, new session); samples taken while offline are dropped,
  not sent late.

**Units.** The kit sends 10-bit ADC counts: `adc = {"zero": 512, "gain": 225.0, "units": "adu"}`. The gain is
*nominal, not calibrated* — the SparkFun board amplifies ≈1100×, and 1 mV × 1100 on a 5 V / 1023-count ADC is
≈225 counts. Amplitudes in mV are therefore approximate (timing and rhythm are unaffected). Use
`--adc-gain 341` with a 3.3 V analog reference, or your measured value.

Options: `--port` (required), `--baud 115200`, `--fs 250`, `--device`, `--key`, `--url`, `--frame-ms 100`,
`--adc-bits 10`, `--adc-zero 512`, `--adc-gain 225`, `--max-retries 0` (forever), `--rate-window 10`,
`--banner-wait 2.5`, `--quiet`.

## Many streams (soak test)

```bash
python sender/multi_sender.py --files 'data/records/*.csv' -n 50 --url ws://localhost:8000 \
    --device-template 'dev-soak-{i:03d}' --key-template 'soak-key-{i:03d}' --loop --duration 14400
```

Records are assigned round-robin; `{i}` is the stream number and `{record}` the file stem. The devices must
exist (be paired) on the server. Every other `stream_sender.py` option is passed through to all streams.
Prints an aggregate line (`streams live/done/failed | frames/s samples/s | reconnects`); `--verbose` adds the
per-stream events. 50 real-time streams are 500 frames/s and use a few percent of one core.

## Testing without the backend

```bash
python sender/mock_server.py --port 8000                                        # accepts any non-empty key
python sender/mock_server.py --port 8000 --keys dev-demo-208=demo-key-208       # enforce keys
~/.venvs/cardiosense/bin/python -m pytest sender/tests -q                       # ~70 s (two 20 s pacing runs); -s shows the numbers
```

The mock validates hello and every frame, drops duplicate `seq`, records `t0` jumps as gaps, rejects `t0`
going backwards, replies `ack`, and prints totals every 5 s.
