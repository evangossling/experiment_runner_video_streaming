# Video Streaming Experiment Runner

Two-machine experiment runner for streaming 4K video over a multipath QUIC tunnel (Starlink + Quectel cellular). It runs a list of tunnel configurations (scheduler, FEC, etc.) for N trials each, loads the links with iperf3, streams a video, and logs everything.

## Quick start

1. Copy `runner.py` and `config.py` into the same directory on **both** the client and the server. Both machines must use an identical `config.py` so experiment names and trial numbering match.
2. Edit `config.py` (see [Key config](#key-config)).
3. Make sure these exist on the machines:
   - **Server:** the tunnel app (`SERVER_TUNNEL_DIR`, with `cert.pem`/`key.pem`), `receiver.py` in `VIDEO_APP_DIR`, and a standing `iperf3 -s -p 5201` (unless `START_IPERF_SERVER = True`).
   - **Client:** the tunnel app and wrapper (`TUNNEL_APP_DIR`), the sidecar (`~/tunnel_sidecar` with its venv and `serve.py`), `sender.py` and the video file in `VIDEO_APP_DIR`.
4. Confirm the server's control port is free: `sudo ss -ltnp | grep :1234`

## How to run

Both sides need root (the runner does not use per-command `sudo`). Start the server first, then the client shortly after:

```bash
# server
sudo python3 runner.py server

# client
sudo python3 runner.py client
```

`Ctrl+C` on either side stops all child processes cleanly.

## Key config

All settings live in [config.py](config.py).

| Setting | Purpose |
|---|---|
| `SERVER_IP`, `CLIENT_IP` | Machine addresses |
| `CONTROL_PORT` | TCP port for client/server sync (default 1234) |
| `STARLINK_ADDR`, `QUECTEL_ADDR` | Client path addresses passed to the tunnel |
| `REQUIRED_STARLINK_PUBLIC_IP` | Client aborts if its Starlink public IP (via `eno4`) differs |
| `VIDEO_APP_DIR`, `VIDEO_FILE`, `VIDEO_WIDTH/HEIGHT/FPS/BITRATE/PRESET` | Video app and stream settings |
| `RECEIVER_NO_SAVE_VIDEO`, `ESTIMATED_VIDEO_GB` | Whether to save received video; used for the server disk-space check |
| `IPERF_*` | Background load: 20M warm-up (`IPERF_INITIAL_*`, `IPERF_EXTRA_SECONDS`) and 140M pre-video burst (`IPERF_HIGH_*`), packet length, pause and retries |
| `START_IPERF_SERVER` | Let the runner start `iperf3 -s` instead of using the standing one |
| `TRACE_LEVEL` | Tunnel trace level (`summary`, `events`, `packets`, `full`, or `None` to disable) |
| `*_SECONDS` timing values | Pauses between trials/experiments, video timeout, etc. |
| `EXPERIMENTS` | The list of experiments (below) |

### Defining experiments

Each entry in `EXPERIMENTS`:

```python
{
    "name": "12_qoepredictive_fec_new",   # output folder name
    "trials": 11,
    "client_tunnel_args": "--packet-scheduler qoepredictive ... --fec --fec-profile p30",
    "server_tunnel_args": "--fec --fec-profile p30",
    "needs_inferred": True,   # wait for sidecar "Inferred" lines for both paths
    # "trace_level": "packets",   # optional per-experiment override
}
```

- `client_tunnel_args` / `server_tunnel_args` are raw flag strings appended to the tunnel commands (parsed with `shlex`).
- Set `needs_inferred` to `True` for sidecar/prediction-based schedulers. It is not inferred from the args.

## Architecture

```
 Client                                   Server
 ------                                   ------
 serve.py (sidecar)                       iperf3 -s
 tunnel-client  ==== QUIC multipath ====  tunnel-server
   (Starlink + Quectel)       tun0 10.0.0.1
 iperf3 -c   ------- UDP load ---------->
 sender.py   ------- video ------------>  receiver.py
      \________ TCP control channel ________/
```

`runner.py` is a single script with a `server` and a `client` role. The client drives the sequence over a line-based TCP control channel.

**Per experiment:** server and client stop any old tunnel, then the server starts its tunnel followed by the client.

**Per trial:**
1. If `needs_inferred`: 20M iperf for 90 s (plus 60 s more if the sidecar has not yet reported both paths).
2. 140M iperf for 30 s, then a short wait.
3. Server starts `receiver.py`; client starts `sender.py` and streams the video.
4. Server waits for the receiver to finish saving.

## Output

Under `VIDEO_APP_DIR/<experiment>/` on each machine:

- `<n>/`: the receiver's numbered video output (server). Leftover numbered folders from earlier runs shift this numbering, so the runner warns about them.
- `run_logs/<trial>/`: tunnel, iperf, sender and receiver logs, plus `trial_info.txt` (including any warnings).
- `run_logs/`: tunnel traces (`{client,server}_trace.jsonl`) and `phases.csv`, which has per-trial phase timestamps for slicing the trace.

iperf failures are retried and never abort a trial. Other failures stop the run.
