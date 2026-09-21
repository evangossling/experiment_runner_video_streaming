#!/usr/bin/env python3
"""
Two-machine experiment runner.

Run on server:
    sudo python3 runner.py server

Run on client:
    sudo python3 runner.py client

Both must be run with sudo: no passwordless sudo is configured, so this
runner itself must already be root -- every child process it spawns
(tunnel-server, receiver.py, tunnel-client, serve.py, ip route) then
inherits root and needs no per-command "sudo" prefix or privilege
escalation of its own.

Start the server first, then the client (manually, roughly together).

The server is assumed to already be running `iperf3 -s -p CFG.IPERF_PORT`
all the time (e.g. as a daemon), independently of this runner -- the
runner never starts or stops it.

serve.py (the sidecar) is started once on the client, right at the start
of the run, after its usual warmup pause -- and is left running for the
entire duration of the run (every experiment, every trial), only being
stopped at the very end during cleanup.

Per EXPERIMENT (not per trial):
  1. Both sides restart their tunnel with the experiment's config.
  2. Client runs the iperf warm-up ONCE:
       - if the experiment's needs_inferred is true: 20 Mbps iperf3 for
         IPERF_INITIAL_SECONDS (2 minutes) first (assumed to be enough for
         both links to be inferred -- the sidecar output is not checked);
       - then IPERF_HIGH_BANDWIDTH for IPERF_HIGH_SECONDS, always.
     Each iperf3 run is retried a few times if it fails to start (the
     iperf3 server can still be finishing the previous test, which shows
     up as "unable to send control message: Broken pipe").
  3. Client tells the server WARMUP_DONE, then all trials run back to back
     (BETWEEN_TRIALS_SECONDS apart) with NO iperf in between.

The control channel is TCP CONTROL_PORT (config). iperf3 is separate,
UDP CFG.IPERF_PORT.
"""

import argparse
import datetime
import importlib.util
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    here = Path(__file__).resolve().parent
    path = here / "config.py"
    if not path.exists():
        raise RuntimeError(f"Missing config file: {path}")
    spec = importlib.util.spec_from_file_location("experiment_config", path)
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


CFG = load_config()


# ---------------------------------------------------------------------------
# Globals / process management
#
# Each tracked process is (proc, name). The runner itself is invoked with
# sudo (see main()), so every child process it spawns already runs as
# root -- plain signals work fine, no separate `sudo kill` path needed.
# ---------------------------------------------------------------------------

processes = []
stop_event = threading.Event()
log_threads = []


def expand(path):
    return os.path.abspath(os.path.expanduser(path))


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text):
    return ANSI_RE.sub("", text)


# Best-effort request to disable ANSI color in subprocess output. Rust's
# env_logger (used by the tunnel binaries) honors RUST_LOG_STYLE; NO_COLOR
# and CLICOLOR cover most other CLI tools. Logs are also stripped of any
# ANSI that gets through anyway (see strip_ansi / tee_process_output),
# since not every tool respects these.
NO_COLOR_ENV = {"NO_COLOR": "1", "RUST_LOG_STYLE": "never", "CLICOLOR": "0"}


def unregister(proc):
    processes[:] = [p for p in processes if p[0] is not proc]


def terminate_process(proc, name, timeout=8):
    if proc is None or proc.poll() is not None:
        return

    print(f"[runner] stopping {name} (pid {proc.pid})", flush=True)

    def send(sig):
        try:
            proc.send_signal(getattr(signal, f"SIG{sig}"))
        except ProcessLookupError:
            pass

    send("INT")
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass

    print(f"[runner] {name} did not exit on SIGINT; escalating", flush=True)
    send("TERM")
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass

    print(f"[runner] {name} still alive; sending SIGKILL", flush=True)
    send("KILL")
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def cleanup():
    stop_event.set()

    # Reverse order: application -> receiver/iperf -> tunnel -> sidecar.
    for proc, name in reversed(list(processes)):
        terminate_process(proc, name)

    processes.clear()


def on_signal(signum, frame):
    print(f"\n[runner] received signal {signum}; cleaning up...", flush=True)
    cleanup()
    raise SystemExit(130)


signal.signal(signal.SIGINT, on_signal)
signal.signal(signal.SIGTERM, on_signal)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def tee_process_output(proc, log_file, prefix="", monitor=None, name="process"):
    """
    Drain a process's stdout/stderr, save it, optionally feed a line
    monitor callback object, and print it. Prevents subprocess pipes from filling
    (which is what silently stalls a process whose output nobody reads).
    Strips ANSI color codes before writing/matching, and brackets the log
    with explicit start/stop timestamps so a truncated or crashed run is
    easy to bound just by reading the file.
    """
    def worker():
        try:
            with open(log_file, "a", buffering=1) as f:
                f.write(f"=== {name} started {now_iso()} (pid {proc.pid}) ===\n")
                for line in iter(proc.stdout.readline, ""):
                    if not line:
                        break
                    line = strip_ansi(line)
                    f.write(line)
                    f.flush()
                    if monitor is not None:
                        monitor.feed(line)
                    print(f"{prefix}{line}", end="", flush=True)
                rc = proc.poll()
                rc_str = str(rc) if rc is not None else "unknown (stdout closed, process still alive)"
                f.write(f"=== {name} stdout closed {now_iso()} (exit code {rc_str}) ===\n")
        except Exception as e:
            print(f"[runner] log thread error ({prefix.strip()}): {e}",
                  flush=True)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    log_threads.append(t)


def start_logged(cmd, cwd=None, log_file=None, env=None, name="process",
                  monitor=None):
    print(f"[runner] starting {name}:", flush=True)
    print("         " + " ".join(shlex.quote(str(x)) for x in cmd), flush=True)

    merged_env = os.environ.copy()
    merged_env.update(NO_COLOR_ENV)
    if env:
        merged_env.update(env)

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    processes.append((proc, name))

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        tee_process_output(proc, log_file, prefix=f"[{name}] ", monitor=monitor,
                            name=name)

    return proc


# ---------------------------------------------------------------------------
# Generic command helpers (blocking, with an ENFORCED timeout)
# ---------------------------------------------------------------------------

def run_command(cmd, cwd=None, timeout=None, log_file=None, env=None,
                 name="command", check=True):
    print(f"[runner] running {name}:", flush=True)
    print("         " + " ".join(shlex.quote(str(x)) for x in cmd), flush=True)

    merged_env = os.environ.copy()
    merged_env.update(NO_COLOR_ENV)
    if env:
        merged_env.update(env)

    log_fh = None
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_file, "a", buffering=1)
        log_fh.write(f"=== {name} started {now_iso()} ===\n")

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    processes.append((proc, name))

    output = []

    def reader():
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                line = strip_ansi(line)
                output.append(line)
                if log_fh:
                    log_fh.write(line)
                    log_fh.flush()
                print(f"[{name}] {line}", end="", flush=True)
        except Exception as e:
            print(f"[runner] {name} reader error: {e}", flush=True)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    deadline = (time.monotonic() + timeout) if timeout else None
    timed_out = False
    while t.is_alive():
        if stop_event.is_set():
            terminate_process(proc, name)
            break
        if deadline is not None and time.monotonic() > deadline:
            print(f"[runner] {name} exceeded {timeout}s timeout; stopping it",
                  flush=True)
            timed_out = True
            terminate_process(proc, name)
            break
        t.join(timeout=0.25)

    rc = proc.poll()
    if rc is None:
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rc = -1

    if log_fh:
        log_fh.write(f"=== {name} ended {now_iso()} (exit code {rc}) ===\n")
        log_fh.close()

    unregister(proc)

    if stop_event.is_set():
        raise RuntimeError("Stopped")

    if timed_out:
        raise RuntimeError(f"{name} timed out after {timeout}s")

    if check and rc != 0:
        raise RuntimeError(f"{name} exited with code {rc}")

    return rc, "".join(output)


def wait_or_stop(seconds):
    end = time.monotonic() + seconds
    while True:
        if stop_event.is_set():
            raise RuntimeError("Stopped")
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


# ---------------------------------------------------------------------------
# Network checks
# ---------------------------------------------------------------------------

def check_client_public_ip():
    rc, output = run_command(
        ["curl", "--interface", "eno4", "--fail", "--silent", "--show-error",
         "https://api.ipify.org"],
        timeout=15,
        name="Starlink public-IP check",
        check=False,
    )
    public_ip = output.strip().splitlines()[-1].strip() if output.strip() else ""

    if rc != 0:
        raise RuntimeError(
            "Could not determine the Starlink public IP using "
            "'curl --interface eno4 https://api.ipify.org'."
        )

    if public_ip != CFG.REQUIRED_STARLINK_PUBLIC_IP:
        raise RuntimeError(
            f"Starlink public IP check failed: expected "
            f"{CFG.REQUIRED_STARLINK_PUBLIC_IP}, got {public_ip!r}. "
            "The server-side firewall only allows the expected IP."
        )

    print(f"[runner] Starlink public IP verified: {public_ip}", flush=True)


def ensure_client_route():
    """
    Add the sidecar route only when it is absent. Existing routes are not
    modified or removed.
    """
    result = subprocess.run(
        ["ip", "route", "show", "192.168.100.0/24"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError("Could not inspect the 192.168.100.0/24 route")

    if "192.168.100.0/24" in result.stdout:
        print("[runner] sidecar route already exists", flush=True)
        return

    print("[runner] adding sidecar route", flush=True)
    subprocess.run(
        ["ip", "route", "add", "192.168.100.0/24",
         "via", "192.168.1.1", "dev", "eno4"],
        check=True,
    )


# ---------------------------------------------------------------------------
# Control channel: a persistent buffered line reader per socket.
#
# The earlier version read one recv() chunk and discarded everything past
# the first newline. If two messages arrived in the same chunk (easy, on
# localhost-speed links), the second message vanished. This buffers any
# leftover bytes between calls.
# ---------------------------------------------------------------------------

class LineSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def send_line(self, message):
        self.sock.sendall((message + "\n").encode())

    def recv_line(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("control connection closed")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode(errors="replace").strip()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class ControlServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.client = None  # LineSocket

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(1)
        print(f"[runner] control server listening on {self.host}:{self.port}",
              flush=True)

    def accept(self):
        while not stop_event.is_set():
            try:
                self.sock.settimeout(1)
                raw, addr = self.sock.accept()
                print(f"[runner] control client connected from {addr}", flush=True)
                self.client = LineSocket(raw)
                return self.client
            except socket.timeout:
                continue
        raise RuntimeError("Stopped while waiting for client")

    def close(self):
        if self.client:
            self.client.close()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.client = None
        self.sock = None


class ControlClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.conn = None  # LineSocket

    def connect(self, retries=60):
        last_error = None
        for attempt in range(retries):
            if stop_event.is_set():
                raise RuntimeError("Stopped")
            try:
                raw = socket.create_connection((self.host, self.port), timeout=5)
                self.conn = LineSocket(raw)
                print(
                    f"[runner] connected to server control channel "
                    f"{self.host}:{self.port}",
                    flush=True,
                )
                return
            except OSError as e:
                last_error = e
                if attempt == 0:
                    print("[runner] waiting for server control channel...",
                          flush=True)
                time.sleep(1)

        raise RuntimeError(
            f"Could not connect to server control channel: {last_error}"
        )

    def close(self):
        if self.conn:
            self.conn.close()
        self.conn = None


# ---------------------------------------------------------------------------
# Tunnel command construction
#
# Fixed, per-machine flags (address, tun name, cert/key, network) live in
# config as named fields. Everything experiment-specific is a raw string
# in EXPERIMENTS[i]["client_tunnel_args"] / ["server_tunnel_args"], parsed
# with shlex. This intentionally does NOT know what flags exist -- it's
# just concatenation, so any flag (including ones not seen before) works.
# ---------------------------------------------------------------------------

def client_tunnel_command(exp):
    cmd = [
        "env",
        "RUST_LOG=tunnel_app::tunnel::metrics=info,tunnel_app::tunnel::control=info",
        CFG.CLIENT_TUNNEL_WRAPPER,
        CFG.CLIENT_TUNNEL_SERVER,
        "-A", CFG.STARLINK_ADDR,
        "-A", CFG.QUECTEL_ADDR,
        "--tun-name", "tun0",
        "--multipath",
    ]
    cmd += shlex.split(exp.get("client_tunnel_args", "") or "")
    return cmd


def server_tunnel_command(exp):
    cmd = [
        "env",
        "RUST_LOG=tunnel_app::tunnel::metrics=info,tunnel_app::tunnel::control=info",
        "./../../../target/release/tunnel-server",
        "--listen", "0.0.0.0:4433",
        "--cert", CFG.SERVER_CERT,
        "--key", CFG.SERVER_KEY,
        "--tunnel-network", "10.0.0.0/24",
        "--tun-name", "tun0",
        "--mtu", "1350",
        "--multipath",
        "--initial-max-path-id", "4",
        "--packet-scheduler", "minrtt",
    ]
    cmd += shlex.split(exp.get("server_tunnel_args", "") or "")
    return cmd


# ---------------------------------------------------------------------------
# Sidecar (client only)
# ---------------------------------------------------------------------------

def start_sidecar(log_file):
    sidecar_dir = expand(CFG.SIDECAR_DIR)
    venv = expand(CFG.SIDECAR_VENV)
    script = expand(CFG.SIDECAR_SCRIPT)

    if not Path(script).exists():
        raise RuntimeError(f"Sidecar script not found: {script}")
    python = str(Path(venv) / "bin" / "python3")
    if not Path(python).exists():
        raise RuntimeError(f"Sidecar venv Python not found: {python}")

    proc = start_logged(
        [python, script],
        cwd=sidecar_dir,
        log_file=str(log_file),
        name="serve.py",
    )
    return proc


# ---------------------------------------------------------------------------
# iperf
# ---------------------------------------------------------------------------

def iperf_command(bandwidth, seconds):
    return [
        "iperf3",
        "-c", CFG.IPERF_SERVER,
        "-p", str(CFG.IPERF_PORT),
        "-u",
        "-b", str(bandwidth),
        "-t", str(seconds),
    ]


def run_iperf(bandwidth, seconds, log_file, label, alive_check=None):
    """
    Run one iperf3 client test, retrying on failure.

    iperf3 servers handle one test at a time. Right after a previous test
    ends (or right after a tunnel restart) the server can drop a new
    control connection mid-handshake, which shows up as
    "iperf3: error - unable to send control message: Broken pipe".
    That's transient, so wait a few seconds and try again instead of
    killing the whole run.

    alive_check: optional callable returning False if retrying is pointless
    (e.g. the tunnel-client process died). Then we fail immediately.
    """
    attempts = max(1, int(getattr(CFG, "IPERF_RETRIES", 5)))
    retry_delay = getattr(CFG, "IPERF_RETRY_DELAY_SECONDS", 10)
    last_error = "unknown"

    for attempt in range(1, attempts + 1):
        try:
            rc, _ = run_command(
                iperf_command(bandwidth, seconds),
                timeout=seconds + 30,
                log_file=log_file,
                name=label,
                check=False,
            )
            if rc == 0:
                if attempt > 1:
                    print(f"[runner] {label} succeeded on attempt {attempt}",
                          flush=True)
                return rc
            last_error = f"exit code {rc}"
        except RuntimeError as e:
            if stop_event.is_set():
                raise  # genuine shutdown -- don't retry
            last_error = str(e)

        print(
            f"[runner] {label} failed (attempt {attempt}/{attempts}): "
            f"{last_error}",
            flush=True,
        )

        if alive_check is not None and not alive_check():
            raise RuntimeError(
                f"{label} failed and the tunnel process is no longer "
                "running; not retrying"
            )

        if attempt < attempts:
            print(f"[runner] retrying {label} in {retry_delay}s", flush=True)
            wait_or_stop(retry_delay)

    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Disk-space check (server only)
# ---------------------------------------------------------------------------

def check_server_disk_space():
    video_dir = expand(CFG.VIDEO_APP_DIR)
    Path(video_dir).mkdir(parents=True, exist_ok=True)

    print("[runner] server filesystem status (df -Th):", flush=True)
    subprocess.run(["df", "-Th"], check=False)

    if CFG.RECEIVER_NO_SAVE_VIDEO:
        print("[runner] --no-save-video enabled; skipping capacity check",
              flush=True)
        return

    expected_trials = sum(int(e["trials"]) for e in CFG.EXPERIMENTS)
    required_gb = expected_trials * float(CFG.ESTIMATED_VIDEO_GB)

    usage = shutil.disk_usage(video_dir)
    free_gb = usage.free / (1024 ** 3)

    print(
        f"[runner] planned saved videos: {expected_trials} x "
        f"{CFG.ESTIMATED_VIDEO_GB:.2f} GB = {required_gb:.2f} GB",
        flush=True,
    )
    print(f"[runner] free space on {video_dir}: {free_gb:.2f} GB", flush=True)

    if required_gb > free_gb:
        raise RuntimeError(
            f"Not enough server disk space. Need approximately "
            f"{required_gb:.2f} GB for {expected_trials} saved videos, "
            f"but only {free_gb:.2f} GB is free on {video_dir}. "
            "Set RECEIVER_NO_SAVE_VIDEO=True in config.py if videos should "
            "not be saved."
        )


def warn_if_stale_trial_dirs(exp_dir):
    """
    receiver.py picks its own numbered output folder based on what already
    exists under exp_dir. If a previous run left folders behind, receiver's
    numbering will drift away from this runner's trial numbering. The
    runner does not pre-create these folders (that was the earlier bug),
    but it also can't safely delete your data, so it just warns loudly.
    """
    if not Path(exp_dir).exists():
        return
    existing = [p.name for p in Path(exp_dir).iterdir()
                if p.is_dir() and p.name.isdigit()]
    if existing:
        print(
            f"[runner] WARNING: {exp_dir} already has numbered folders "
            f"{sorted(existing, key=int)}. receiver.py will continue "
            "numbering from there, which will NOT match this runner's "
            "trial count. Move or delete them before this experiment if "
            "you want trial numbers to line up.",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def server_receiver_command(experiment_name):
    cmd = [
        "python3", "receiver.py",
        "--port", str(CFG.RECEIVER_PORT),
        "--output", experiment_name,
    ]
    if CFG.RECEIVER_NO_SAVE_VIDEO:
        cmd.append("--no-save-video")
    return cmd


def run_server():
    check_server_disk_space()

    # iperf3 -s on CFG.IPERF_PORT is assumed to already be running on the
    # server all the time (started independently of this runner, outside
    # its process lifecycle), so it is neither started nor cleaned up here.
    print(
        f"[runner] assuming an iperf3 server is already running on port "
        f"{CFG.IPERF_PORT} (started independently of this runner)",
        flush=True,
    )

    tunnel_cwd = expand(CFG.SERVER_TUNNEL_DIR)
    video_cwd = expand(CFG.VIDEO_APP_DIR)

    if not CFG.EXPERIMENTS:
        raise RuntimeError("No experiments configured")

    control = ControlServer("0.0.0.0", CFG.CONTROL_PORT)
    control.start()

    current_tunnel = None

    try:
        # Deliberately NOT starting the first tunnel here. It only starts
        # once the client has connected and is about to start its own --
        # see the unified per-experiment block below.
        conn = control.accept()

        hello = conn.recv_line(timeout=120)
        if hello != "CLIENT_READY":
            raise RuntimeError(f"Unexpected client handshake: {hello}")
        conn.send_line("SERVER_READY")

        print("[runner] client/server control handshake complete", flush=True)

        for exp_index, exp in enumerate(CFG.EXPERIMENTS):
            name = exp["name"]
            trials = int(exp["trials"])
            exp_dir = Path(video_cwd) / name
            log_root = exp_dir / CFG.RUNNER_LOG_SUBDIR

            # Every experiment (including the first) goes through the same
            # stop-old / start-new handshake. For the first experiment
            # there's nothing to stop on either side -- the client's ack
            # is immediate -- but this keeps the tunnel's lifetime tightly
            # bound to "right before it's actually used".
            warn_if_stale_trial_dirs(exp_dir)
            print(f"[runner] preparing server tunnel for {name}", flush=True)

            conn.send_line(f"SERVER_RESTART:{name}")

            ack = conn.recv_line(timeout=60)
            if ack != f"CLIENT_TUNNEL_STOPPED:{name}":
                raise RuntimeError(
                    f"Expected tunnel-stop ACK for {name}, got {ack}"
                )

            if current_tunnel:
                terminate_process(current_tunnel, "tunnel-server")
                unregister(current_tunnel)
                current_tunnel = None

            first_log_dir = log_root / "1"
            current_tunnel = start_logged(
                server_tunnel_command(exp),
                cwd=tunnel_cwd,
                log_file=str(first_log_dir / "server_tunnel.log"),
                name="tunnel-server",
            )

            conn.send_line(f"SERVER_TUNNEL_STARTED:{name}")

            # The client now runs this experiment's iperf warm-up (ONCE per
            # experiment, with retries). The server has nothing to do
            # meanwhile except wait for the client to report it's done.
            msg = conn.recv_line(
                timeout=getattr(CFG, "WARMUP_TIMEOUT_SECONDS", 3600)
            )
            if msg != f"WARMUP_DONE:{name}":
                raise RuntimeError(
                    f"Expected WARMUP_DONE for {name}, got {msg}"
                )

            for trial in range(1, trials + 1):
                trial_log_dir = log_root / str(trial)
                trial_log_dir.mkdir(parents=True, exist_ok=True)

                conn.send_line(f"TRIAL_READY:{name}:{trial}")

                msg = conn.recv_line(timeout=180)
                if msg != f"START_RECEIVER:{name}:{trial}":
                    raise RuntimeError(
                        f"Expected START_RECEIVER for {name}/{trial}, got {msg}"
                    )

                receiver_log = trial_log_dir / "receiver.log"
                # NOTE: receiver.py chooses its own numbered subfolder under
                # exp_dir (--output name). This log captures its stdout, not
                # the video it saves.
                receiver = start_logged(
                    server_receiver_command(name),
                    cwd=video_cwd,
                    log_file=str(receiver_log),
                    name="receiver.py",
                )

                wait_or_stop(1.0)
                conn.send_line(f"RECEIVER_STARTED:{name}:{trial}")

                msg = conn.recv_line(timeout=180)
                if msg != f"SENDER_FINISHED:{name}:{trial}":
                    terminate_process(receiver, "receiver.py")
                    unregister(receiver)
                    raise RuntimeError(
                        f"Expected SENDER_FINISHED for {name}/{trial}, got {msg}"
                    )

                try:
                    receiver.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    terminate_process(receiver, "receiver.py")
                unregister(receiver)

                if trial < trials:
                    wait_or_stop(CFG.BETWEEN_TRIALS_SECONDS)

            if exp_index < len(CFG.EXPERIMENTS) - 1:
                wait_or_stop(CFG.BETWEEN_EXPERIMENTS_SECONDS)

        conn.send_line("ALL_EXPERIMENTS_COMPLETE")
        print("[runner] all experiments complete", flush=True)

        try:
            conn.recv_line(timeout=10)
        except Exception:
            pass

    finally:
        try:
            if control.client:
                control.client.send_line("SERVER_SHUTDOWN")
        except Exception:
            pass
        if current_tunnel:
            terminate_process(current_tunnel, "tunnel-server")
        control.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def start_client_tunnel(exp, log_dir):
    cmd = client_tunnel_command(exp)
    log_file = log_dir / "client_tunnel.log"
    return start_logged(
        cmd,
        cwd=expand(CFG.TUNNEL_APP_DIR),
        log_file=str(log_file),
        name="tunnel-client",
    )


def run_client():
    check_client_public_ip()
    ensure_client_route()

    if not CFG.EXPERIMENTS:
        raise RuntimeError("No experiments configured")

    video_cwd = expand(CFG.VIDEO_APP_DIR)
    first_name = CFG.EXPERIMENTS[0]["name"]
    sidecar_log = (Path(video_cwd) / first_name / CFG.RUNNER_LOG_SUBDIR
                   / "sidecar.log")

    # serve.py is started once and runs for the entire duration of the run
    # (all experiments, all trials) -- it is only stopped by cleanup() at
    # the very end. It still gets its warmup pause right here, before
    # anything else on the client starts.
    sidecar = start_sidecar(sidecar_log)
    print(
        f"[runner] sidecar started; waiting {CFG.SIDECAR_STARTUP_SECONDS}s "
        "before use",
        flush=True,
    )
    wait_or_stop(CFG.SIDECAR_STARTUP_SECONDS)

    control = ControlClient(CFG.CONTROL_HOST, CFG.CONTROL_PORT)
    control.connect()
    conn = control.conn
    conn.send_line("CLIENT_READY")

    ready = conn.recv_line(timeout=30)
    if ready != "SERVER_READY":
        raise RuntimeError(f"Unexpected server handshake: {ready}")

    tunnel_proc = None

    try:
        for exp_index, exp in enumerate(CFG.EXPERIMENTS):
            name = exp["name"]
            trials = int(exp["trials"])
            exp_dir = Path(video_cwd) / name
            log_root = exp_dir / CFG.RUNNER_LOG_SUBDIR
            log_root.mkdir(parents=True, exist_ok=True)

            # Every experiment (including the first) goes through the same
            # stop-old / start-new handshake with the server, so the
            # client's tunnel -- like the server's -- only starts right
            # before it's used, not for the whole run.
            msg = conn.recv_line(timeout=180)
            if msg != f"SERVER_RESTART:{name}":
                raise RuntimeError(
                    f"Expected SERVER_RESTART:{name}, got {msg}"
                )

            if tunnel_proc:
                terminate_process(tunnel_proc, "tunnel-client")
                unregister(tunnel_proc)
                tunnel_proc = None

            conn.send_line(f"CLIENT_TUNNEL_STOPPED:{name}")

            msg = conn.recv_line(timeout=180)
            if msg != f"SERVER_TUNNEL_STARTED:{name}":
                raise RuntimeError(
                    f"Expected SERVER_TUNNEL_STARTED:{name}, got {msg}"
                )

            needs_inferred = bool(exp.get("needs_inferred", False))

            first_log_dir = log_root / "1"
            first_log_dir.mkdir(parents=True, exist_ok=True)
            tunnel_proc = start_client_tunnel(exp, first_log_dir)

            wait_or_stop(2)
            if tunnel_proc.poll() is not None:
                raise RuntimeError(
                    f"tunnel-client exited early with code "
                    f"{tunnel_proc.returncode}"
                )

            # ------------------------------------------------------------
            # iperf warm-up: runs ONCE per experiment, right after the
            # tunnel comes up -- NOT before every trial. Lives next to the
            # tunnel logs (trial "1" dir) since it belongs to the
            # experiment as a whole.
            # ------------------------------------------------------------
            iperf_log = first_log_dir / "iperf.log"
            tunnel_alive = lambda p=tunnel_proc: p.poll() is None

            if needs_inferred:
                # No watching of the sidecar's output -- just a fixed
                # low-rate run, assumed long enough for both links to be
                # inferred.
                run_iperf(
                    CFG.IPERF_INITIAL_BANDWIDTH,
                    CFG.IPERF_INITIAL_SECONDS,
                    str(iperf_log),
                    "iperf3-20M-2min",
                    alive_check=tunnel_alive,
                )
                # Give the iperf3 server a moment to finish the previous
                # test and go back to idle before the next connect. (This
                # gap is exactly what was missing when it crashed with
                # "unable to send control message: Broken pipe".)
                wait_or_stop(getattr(CFG, "IPERF_GAP_SECONDS", 5))

            # Always required once per experiment, immediately before the
            # trials (needs_inferred or not).
            run_iperf(
                CFG.IPERF_HIGH_BANDWIDTH,
                CFG.IPERF_HIGH_SECONDS,
                str(iperf_log),
                "iperf3-140M-30s",
                alive_check=tunnel_alive,
            )

            wait_or_stop(CFG.WAIT_AFTER_HIGH_IPERF_SECONDS)
            conn.send_line(f"WARMUP_DONE:{name}")

            # ------------------------------------------------------------
            # Trials: back to back, NO iperf in here.
            # ------------------------------------------------------------
            for trial in range(1, trials + 1):
                trial_log_dir = log_root / str(trial)
                trial_log_dir.mkdir(parents=True, exist_ok=True)

                with open(trial_log_dir / "trial_info.txt", "w") as f:
                    f.write(f"experiment={name}\n")
                    f.write(f"trial={trial}\n")
                    f.write(f"client_tunnel_args={exp.get('client_tunnel_args', '')}\n")
                    f.write(f"needs_inferred={needs_inferred}\n")

                msg = conn.recv_line(timeout=180)
                if msg != f"TRIAL_READY:{name}:{trial}":
                    raise RuntimeError(
                        f"Expected TRIAL_READY for {name}/{trial}, got {msg}"
                    )

                conn.send_line(f"START_RECEIVER:{name}:{trial}")

                msg = conn.recv_line(timeout=180)
                if msg != f"RECEIVER_STARTED:{name}:{trial}":
                    raise RuntimeError(
                        f"Expected RECEIVER_STARTED for {name}/{trial}, got {msg}"
                    )

                wait_or_stop(CFG.RECEIVER_TO_SENDER_SECONDS)

                sender_log = trial_log_dir / "sender.log"
                sender_cmd = [
                    "python3", "sender.py",
                    "--video", CFG.VIDEO_FILE,
                    "--host", "10.0.0.1",
                    "--port", str(CFG.RECEIVER_PORT),
                    "--width", str(CFG.VIDEO_WIDTH),
                    "--height", str(CFG.VIDEO_HEIGHT),
                    "--fps", str(CFG.VIDEO_FPS),
                    "--bitrate", str(CFG.VIDEO_BITRATE),
                ]

                run_command(
                    sender_cmd,
                    cwd=expand(CFG.VIDEO_APP_DIR),
                    timeout=CFG.VIDEO_TIMEOUT_SECONDS,
                    log_file=str(sender_log),
                    name="sender.py",
                    check=True,
                )

                conn.send_line(f"SENDER_FINISHED:{name}:{trial}")

                if trial < trials:
                    wait_or_stop(CFG.BETWEEN_TRIALS_SECONDS)

        msg = conn.recv_line(timeout=30)
        if msg != "ALL_EXPERIMENTS_COMPLETE":
            raise RuntimeError(f"Expected completion message, got {msg}")

        conn.send_line("CLIENT_COMPLETE")
        print("[runner] all experiments complete", flush=True)

    finally:
        control.close()
        if tunnel_proc:
            terminate_process(tunnel_proc, "tunnel-client")
        # Sidecar is deliberately left running until cleanup(), which runs
        # after this function returns/raises.
        _ = sidecar


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["client", "server"])
    args = parser.parse_args()

    if os.geteuid() != 0:
        print(
            "[runner] this must be run as root: sudo python3 runner.py "
            f"{args.role}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    try:
        if args.role == "server":
            run_server()
        else:
            run_client()
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"\n[runner] ERROR: {e}", file=sys.stderr, flush=True)
        cleanup()
        return 1
    finally:
        cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
