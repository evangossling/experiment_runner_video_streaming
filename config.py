# experiment_runner configuration
#
# Copy this file to ~/experiment_runner/config.py on BOTH machines.
# The server and client use the same experiment list so trial numbering
# and experiment names stay identical.

SERVER_IP = "136.64.133.110"
CLIENT_IP = "10.188.32.132"

# TCP control channel used by runner.py for client/server synchronization.
# This is separate from iperf3, which uses UDP 1234. NOTE: 5201 is also
# iperf3's own default TCP listen port -- make sure nothing else on the
# server is bound to it, and that it's open in the gcloud firewall for
# REQUIRED_STARLINK_PUBLIC_IP below.
CONTROL_HOST = SERVER_IP
CONTROL_PORT = 5201

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

# Where the runner puts ITS OWN logs (tunnel output, iperf.log, sender.log,
# trial_info.txt) for each experiment/trial. Deliberately separate from
# RECEIVER_PORT's --output directory below, so the runner never creates a
# numbered folder that would shift receiver.py's own trial numbering.
RUNNER_LOG_SUBDIR = "run_logs"

# Receiver behavior
RECEIVER_PORT = 5000
RECEIVER_NO_SAVE_VIDEO = False

# Estimated size of one saved received video, used only for the disk check.
ESTIMATED_VIDEO_GB = 1.1

# iperf3
IPERF_SERVER = "10.0.0.1"
IPERF_PORT = 1234
IPERF_INITIAL_BANDWIDTH = "20M"
IPERF_INITIAL_SECONDS = 90
IPERF_EXTRA_SECONDS = 60
IPERF_HIGH_BANDWIDTH = "140M"
IPERF_HIGH_SECONDS = 30

# Timing
BETWEEN_TRIALS_SECONDS = 10
BETWEEN_EXPERIMENTS_SECONDS = 10
RECEIVER_TO_SENDER_SECONDS = 5
WAIT_AFTER_HIGH_IPERF_SECONDS = 10
VIDEO_TIMEOUT_SECONDS = 125

# Required Starlink public IP on the client.
# The client checks:
#   curl --interface eno4 https://api.ipify.org
REQUIRED_STARLINK_PUBLIC_IP = "153.66.69.111"

# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
#
# Each experiment gets:
#   ~/video_streaming_app/<name>/<trial>          <- receiver.py's own
#                                                     numbered video output
#   ~/video_streaming_app/<name>/run_logs/<trial> <- runner-authored logs
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

EXPERIMENTS = [
    {
        "name": "predictive_model_p30",
        "trials": 3,
        "client_tunnel_args": (
            "--packet-scheduler predictiveminrtt "
            "--path-model sidecar "
            "--prediction-mode model "
            "--sidecar-sock /tmp/fec_model.sock "
            "--sidecar-provider quectel=10.189.55.160 "
            "--sidecar-provider starlink=192.168.1.241 "
            "--fec --fec-profile p30"
        ),
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": True,
    },
    {
        "name": "predictive_static_p30",
        "trials": 3,
        "client_tunnel_args": (
            "--packet-scheduler predictiveminrtt "
            "--path-model sidecar "
            "--prediction-mode static "
            "--sidecar-sock /tmp/fec_model.sock "
            "--sidecar-provider quectel=10.189.55.160 "
            "--sidecar-provider starlink=192.168.1.241 "
            "--fec --fec-profile p30"
        ),
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": True,
    },
    {
        "name": "minrtt_p30",
        "trials": 3,
        "client_tunnel_args": "--packet-scheduler minrtt --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": False,
    },
]
