# experiment_runner configuration
#
# Copy this file to sit alongside runner.py on BOTH machines (whatever
# directory that is -- load_config() finds it next to runner.py, not by a
# fixed folder name).
# The server and client use the same experiment list so trial numbering
# and experiment names stay identical.
 
SERVER_IP = "136.64.133.110"
CLIENT_IP = "10.188.32.132"
 
# TCP control channel used by runner.py for client/server synchronization.
# Binds SERVER_IP (the host's real interface). iperf3's server binds
# 0.0.0.0 for BOTH its TCP control handshake and UDP data, on every
# interface -- so this control channel and the iperf3 test both compete
# for the same TCP port number if they're set to the same value, even
# though iperf3's UDP *data* target is a different address (10.0.0.1 over
# tun0). Keeping this on 1234 and IPERF_PORT on 5201 (the port the
# standing manual `iperf3 -s` already listens on) avoids that collision.
# TCP is only open through the firewall on 5201 and 1234; the standing
# manual iperf3 -s permanently holds TCP 5201, so 1234 is what's left for
# this. Confirm nothing else has it before running:
#   sudo ss -ltnp | grep :1234
CONTROL_HOST = SERVER_IP
CONTROL_PORT = 1234
 
# Client-side tunnel sidecar
SIDECAR_DIR = "~/tunnel_sidecar"
SIDECAR_VENV = "~/tunnel_sidecar/venv"
SIDECAR_SCRIPT = "~/tunnel_sidecar/serve.py"
SIDECAR_STARTUP_SECONDS = 60
 
# Client-side tunnel application
TUNNEL_APP_DIR = "~/quiche/tokio-quiche/examples/tunnel-app"
CLIENT_TUNNEL_WRAPPER = "./tunnel-client-wrapped.sh"
CLIENT_TUNNEL_SERVER = f"{SERVER_IP}:4433"
 
# Client network interfaces/addresses used by the tunnel. These are fixed
# per-machine, so they stay here rather than in each experiment's arg
# string.
STARLINK_ADDR = "192.168.1.241:0"
QUECTEL_ADDR = "10.189.55.160:0"
 
# Server-side tunnel application
SERVER_TUNNEL_DIR = "~/quiche/tokio-quiche/examples/tunnel-app"
SERVER_CERT = "cert.pem"
SERVER_KEY = "key.pem"
 
# Video application
VIDEO_APP_DIR = "~/video_streaming_app"
VIDEO_FILE = "videos/4k_30fps_120s.mp4"
VIDEO_WIDTH = 3840
VIDEO_HEIGHT = 2160
VIDEO_FPS = 30
VIDEO_BITRATE = 45000
VIDEO_PRESET = "ultrafast"   # x264 speed preset passed to sender.py --preset
 
# Where the runner puts ITS OWN logs (tunnel output, iperf.log, sender.log,
# trial_info.txt) for each experiment/trial. Deliberately separate from
# RECEIVER_PORT's --output directory below, so the runner never creates a
# numbered folder that would shift receiver.py's own trial numbering.
RUNNER_LOG_SUBDIR = "run_logs"
 
# Receiver behavior
RECEIVER_PORT = 5000
RECEIVER_NO_SAVE_VIDEO = False
 
# How long the server waits for receiver.py to finish saving after the video
# ends. receiver.py runs with --once, so it exits on its own; this is only
# the limit before the runner stops it.
RECEIVER_EXIT_WAIT_SECONDS = 45

# Estimated size of one saved received video, used only for the disk check.
ESTIMATED_VIDEO_GB = 1.1
 
# iperf3
IPERF_SERVER = "10.0.0.1"
IPERF_PORT = 5201  # matches the standing manual `iperf3 -s` on the server
# If True, the runner starts its own `iperf3 -s -p IPERF_PORT` on the
# server for the whole run instead of relying on a manually-started one.
# Leave False to use the standing manual server on 5201 as-is. If you flip
# this on, either point IPERF_PORT at a genuinely free port (and make sure
# it's allowed through the firewall) or stop the manual server on 5201
# first -- two processes can't both bind the same TCP+UDP port.
START_IPERF_SERVER = False
IPERF_INITIAL_BANDWIDTH = "20M"
IPERF_INITIAL_SECONDS = 90
IPERF_EXTRA_SECONDS = 60
IPERF_HIGH_BANDWIDTH = "140M"
IPERF_HIGH_SECONDS = 30
# UDP payload per iperf3 packet. The default (1460) is larger than the
# tunnel's MTU (1306, or 1297 with FEC), so each packet would be split in two
# inside the tunnel.
IPERF_LENGTH = 1200

# Tunnel trace (--trace-file / --trace-level), one file per experiment and
# side, in ~/video_streaming_app/<name>/run_logs/. The tunnel runs for a whole
# experiment, so one trace covers all its trials; phases.csv in the same
# folder gives each trial's phase times for cutting the trace. Levels:
# summary, events, packets (very large), full. An experiment can override this
# with its own "trace_level". Set to None to disable tracing.
TRACE_LEVEL = "events"
 
# Timing
BETWEEN_TRIALS_SECONDS = 10
BETWEEN_EXPERIMENTS_SECONDS = 10
RECEIVER_TO_SENDER_SECONDS = 5
WAIT_AFTER_HIGH_IPERF_SECONDS = 10
VIDEO_TIMEOUT_SECONDS = 125
 
# Required Starlink public IP on the client.
# The client checks:
#   curl --interface eno4 https://api.ipify.org
REQUIRED_STARLINK_PUBLIC_IP = "153.66.69.182"
 
# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
#
# Each experiment gets:
#   ~/video_streaming_app/<name>/<trial>          <- receiver.py's own
#                                                     numbered video output
#   ~/video_streaming_app/<name>/run_logs/<trial> <- runner-authored logs
#   ~/video_streaming_app/<name>/run_logs/         <- tunnel traces and
#                                                     phases.csv
#
# client_tunnel_args / server_tunnel_args are raw strings: everything you'd
# type after `--multipath` on the client, or after `--packet-scheduler
# minrtt` on the server. Parsed with shlex, so normal quoting works. Leave
# either one empty ("") if an experiment needs no extra flags at all.
#
# needs_inferred controls whether the runner waits for BOTH
# "Inferred starlink/path=0" and "Inferred quectel/path=1" (from serve.py)
# before the high-rate iperf. Set this explicitly per experiment -- it is
# NOT inferred from the args string, since you may use flags the runner
# doesn't parse.


SIDECAR_PROVIDERS_ARGS = (
    "--sidecar-sock /tmp/fec_model.sock "
    "--sidecar-provider quectel=10.189.55.160 "
    "--sidecar-provider starlink=192.168.1.241"
)

EXPERIMENTS = [
    
    {
        "name": "11_qoepredictive_new",
        "trials": 11,
        "client_tunnel_args": f"--packet-scheduler qoepredictive --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "12_qoepredictive_fec_new",
        "trials": 11,
        "client_tunnel_args": f"--packet-scheduler qoepredictive --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS} --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": True,
    },
    {
        "name": "1_sp_new",
        "trials": 11,
        "client_tunnel_args": "--packet-scheduler singlepath",
        "server_tunnel_args": "",
        "needs_inferred": False,
    },
    {
        "name": "2_sp_fec_new",
        "trials": 11,
        "client_tunnel_args": "--packet-scheduler singlepath --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": False,
    },
    {
        "name": "5_minrtt_new",
        "trials": 11,
        "client_tunnel_args": "--packet-scheduler minrtt",
        "server_tunnel_args": "",
        "needs_inferred": False,
    },
    {
        "name": "6_minrtt_fec_new",
        "trials": 11,
        "client_tunnel_args": "--packet-scheduler minrtt --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": False,
    },
    {
        "name": "9_predictiveminrtt_new",
        "trials": 11,
        "client_tunnel_args": f"--packet-scheduler predictiveminrtt --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "10_predictiveminrtt_fec_new",
        "trials": 11,
        "client_tunnel_args": f"--packet-scheduler predictiveminrtt --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS} --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": True,
    },
    {
        "name": "7_modelminrtt_new",
        "trials": 11,
        "client_tunnel_args": f"--packet-scheduler modelminrtt --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "8_modelminrtt_fec_new",
        "trials": 11,
        "client_tunnel_args": f"--packet-scheduler modelminrtt --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS} --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": True,
    },
    {
        "name": "3_roundrobin_new",
        "trials": 11,
        "client_tunnel_args": "--packet-scheduler roundrobin",
        "server_tunnel_args": "",
        "needs_inferred": False,
    },
    {
        "name": "4_roundrobin_fec_new",
        "trials": 11,
        "client_tunnel_args": "--packet-scheduler roundrobin --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": False,
    },

]