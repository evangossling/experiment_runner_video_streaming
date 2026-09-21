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
# The server binds 0.0.0.0:CONTROL_PORT; the client connects to
# CONTROL_HOST:CONTROL_PORT. iperf3's server binds 0.0.0.0 for BOTH its TCP
# control handshake and UDP data, so this control channel and the iperf3
# test would collide if they used the same TCP port number. Keeping this on
# 1234 and IPERF_PORT on 5201 (the port the standing iperf3 -s daemon
# already listens on) avoids that.
# TCP is only open through the firewall on 5201 and 1234; the standing
# iperf3 -s permanently holds TCP 5201, so 1234 is what's left for this.
# Confirm nothing else has it before running:
#   sudo ss -ltnp | grep :1234
CONTROL_HOST = SERVER_IP
CONTROL_PORT = 1234

# Client-side tunnel sidecar
SIDECAR_DIR = "/opt/evan/tunnel_sidecar"
SIDECAR_VENV = "/opt/evan/tunnel_sidecar/venv"
SIDECAR_SCRIPT = "/opt/evan/tunnel_sidecar/serve.py"
SIDECAR_STARTUP_SECONDS = 60

# Client-side tunnel application
TUNNEL_APP_DIR = "/opt/evan/quiche/tokio-quiche/examples/tunnel-app"
CLIENT_TUNNEL_WRAPPER = "./tunnel-client-wrapped.sh"
CLIENT_TUNNEL_SERVER = f"{SERVER_IP}:4433"

# Client network interfaces/addresses used by the tunnel. These are fixed
# per-machine, so they stay here rather than in each experiment's arg
# string.
STARLINK_ADDR = "192.168.1.241:0"
QUECTEL_ADDR = "10.189.55.160:0"

# Server-side tunnel application
SERVER_TUNNEL_DIR = "/opt/evan/quiche/tokio-quiche/examples/tunnel-app"
SERVER_CERT = "cert.pem"
SERVER_KEY = "key.pem"

# Video application
VIDEO_APP_DIR = "/opt/evan/video_streaming_app"
VIDEO_FILE = "videos/4k_30fps_120s.mp4"
VIDEO_WIDTH = 3840
VIDEO_HEIGHT = 2160
VIDEO_FPS = 30
VIDEO_BITRATE = 45000

# Where the runner puts ITS OWN logs (tunnel output, iperf.log, sender.log,
# trial_info.txt) for each experiment/trial. Deliberately separate from
# RECEIVER_PORT's --output directory below, so the runner never creates a
# numbered folder that would shift receiver.py's own trial numbering.
# Note: the per-experiment files (client/server tunnel logs and iperf.log)
# live in run_logs/1/, since they belong to the whole experiment.
RUNNER_LOG_SUBDIR = "run_logs"

# Receiver behavior
RECEIVER_PORT = 5000
RECEIVER_NO_SAVE_VIDEO = False

# Estimated size of one saved received video, used only for the disk check.
ESTIMATED_VIDEO_GB = 1.1

# iperf3
# The runner NEVER starts or stops the iperf3 server: it assumes the
# standing `iperf3 -s -p IPERF_PORT` daemon on the server is always up.
IPERF_SERVER = "10.0.0.1"
IPERF_PORT = 5201
IPERF_INITIAL_BANDWIDTH = "20M"
IPERF_INITIAL_SECONDS = 120
IPERF_HIGH_BANDWIDTH = "140M"
IPERF_HIGH_SECONDS = 30

# iperf runs ONCE per experiment (right after that experiment's tunnel
# starts), not per trial. Sequence: [20M for 2 min if needs_inferred] ->
# IPERF_GAP_SECONDS -> 140M for 30 s -> WAIT_AFTER_HIGH_IPERF_SECONDS ->
# trials.
#
# The iperf3 server can still be finishing the previous test when a new
# client connects, which fails with "unable to send control message:
# Broken pipe". These settings avoid/absorb that:
IPERF_GAP_SECONDS = 5          # pause between the 20M and 140M runs
IPERF_RETRIES = 5              # attempts per iperf run before giving up
IPERF_RETRY_DELAY_SECONDS = 10 # wait between attempts
# How long the server waits for the client to finish the whole iperf
# warm-up before declaring the run broken. Must comfortably exceed
# worst-case retries (2 runs x IPERF_RETRIES x ~150 s).
WARMUP_TIMEOUT_SECONDS = 3600

# Timing
BETWEEN_TRIALS_SECONDS = 10
BETWEEN_EXPERIMENTS_SECONDS = 10
RECEIVER_TO_SENDER_SECONDS = 5
WAIT_AFTER_HIGH_IPERF_SECONDS = 10   # once per experiment, after the 140M run
VIDEO_TIMEOUT_SECONDS = 125

# Required Starlink public IP on the client.
# The client checks:
#   curl --interface eno4 https://api.ipify.org
REQUIRED_STARLINK_PUBLIC_IP = "153.66.69.119"

# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
#
# Each experiment gets:
#   /opt/evan/video_streaming_app/<name>/<trial>          <- receiver.py's own
#                                                     numbered video output
#   /opt/evan/video_streaming_app/<name>/run_logs/<trial> <- runner-authored logs
#
# client_tunnel_args / server_tunnel_args are raw strings: everything you'd
# type after `--multipath` on the client, or after `--packet-scheduler
# minrtt` on the server. Parsed with shlex, so normal quoting works. Leave
# either one empty ("") if an experiment needs no extra flags at all.
#
# needs_inferred controls whether the runner does the extra 20M / 2-minute
# iperf run (once, at the start of the experiment) before the 140M run.
# The runner does NOT check the sidecar's "Inferred starlink/path=0" /
# "Inferred quectel/path=1" output any more -- the 2 minutes is simply
# assumed to be enough. Set this explicitly per experiment -- it is NOT
# inferred from the args string, since you may use flags the runner doesn't
# parse.


SIDECAR_PROVIDERS_ARGS = (
    "--sidecar-sock /tmp/fec_model.sock "
    "--sidecar-provider quectel=10.189.55.160 "
    "--sidecar-provider starlink=192.168.1.241"
)

EXPERIMENTS = [
    {
        "name": "arrivaltime_model",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode model {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "arrivaltime_live",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode live {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": False,  # unconfirmed for "live"
    },
    {
        "name": "arrivaltime_static",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "predictiveminrtt_model",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler predictiveminrtt --path-model sidecar --prediction-mode model {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "predictiveminrtt_live",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler predictiveminrtt --path-model sidecar --prediction-mode live {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": False,  # unconfirmed for "live"
    },
    {
        "name": "predictiveminrtt_static",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler predictiveminrtt --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "arrivaltime_p30",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS} --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": True,
    },
    {
        "name": "arrivaltime_p60",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS} --fec --fec-profile p60",
        "server_tunnel_args": "--fec --fec-profile p60",
        "needs_inferred": True,
    },
    {
        "name": "arrivaltime_cloud",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode static {SIDECAR_PROVIDERS_ARGS} --fec --fec-profile cloud-gaming",
        "server_tunnel_args": "--fec --fec-profile cloud-gaming",
        "needs_inferred": True,
    },
    {
        "name": "minrtt_p30",
        "trials": 10,
        "client_tunnel_args": "--packet-scheduler minrtt --fec --fec-profile p30",
        "server_tunnel_args": "--fec --fec-profile p30",
        "needs_inferred": False,
    },
    {
        "name": "minrtt_p60",
        "trials": 10,
        "client_tunnel_args": "--packet-scheduler minrtt --fec --fec-profile p60",
        "server_tunnel_args": "--fec --fec-profile p60",
        "needs_inferred": False,
    },
    {
        "name": "minrtt_cloud",
        "trials": 10,
        "client_tunnel_args": "--packet-scheduler minrtt --fec --fec-profile cloud-gaming",
        "server_tunnel_args": "--fec --fec-profile cloud-gaming",
        "needs_inferred": False,
    },
    {
        "name": "arrivaltime_kalman_model",
        "trials": 10,
        "client_tunnel_args": "--packet-scheduler arrivaltime --path-model kalman --prediction-mode model",
        "server_tunnel_args": "",
        "needs_inferred": False,  # kalman doesn't use the sidecar
    },
    {
        "name": "arrivaltime_sidecar_model",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler arrivaltime --path-model sidecar --prediction-mode model {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "predictiveminrtt_kalman_model",
        "trials": 10,
        "client_tunnel_args": "--packet-scheduler predictiveminrtt --path-model kalman --prediction-mode model",
        "server_tunnel_args": "",
        "needs_inferred": False,  # kalman doesn't use the sidecar
    },
    {
        "name": "predictiveminrtt_sidecar_model",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler predictiveminrtt --path-model sidecar --prediction-mode model {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
    {
        "name": "modelminrtt_kalman_model",
        "trials": 10,
        "client_tunnel_args": "--packet-scheduler modelminrtt --path-model kalman --prediction-mode model",
        "server_tunnel_args": "",
        "needs_inferred": False,  # kalman doesn't use the sidecar
    },
    {
        "name": "modelminrtt_sidecar_model",
        "trials": 10,
        "client_tunnel_args": f"--packet-scheduler modelminrtt --path-model sidecar --prediction-mode model {SIDECAR_PROVIDERS_ARGS}",
        "server_tunnel_args": "",
        "needs_inferred": True,
    },
]

#EXPERIMENTS = [
#    {
#        "name": "predictive_model_p30",
#        "trials": 3,
#        "client_tunnel_args": (
#            "--packet-scheduler predictiveminrtt "
#            "--path-model sidecar "
#            "--prediction-mode model "
#            "--sidecar-sock /tmp/fec_model.sock "
#            "--sidecar-provider quectel=10.189.55.160 "
#            "--sidecar-provider starlink=192.168.1.241 "
#            "--fec --fec-profile p30"
#        ),
#        "server_tunnel_args": "--fec --fec-profile p30",
#        "needs_inferred": True,
#    },
#    {
#        "name": "predictive_static_p30",
#        "trials": 3,
#        "client_tunnel_args": (
#            "--packet-scheduler predictiveminrtt "
#            "--path-model sidecar "
#            "--prediction-mode static "
#            "--sidecar-sock /tmp/fec_model.sock "
#            "--sidecar-provider quectel=10.189.55.160 "
#            "--sidecar-provider starlink=192.168.1.241 "
#            "--fec --fec-profile p30"
#        ),
#        "server_tunnel_args": "--fec --fec-profile p30",
#        "needs_inferred": True,
#    },
#    {
#        "name": "minrtt_p30",
#        "trials": 3,
#        "client_tunnel_args": "--packet-scheduler minrtt --fec --fec-profile p30",
#        "server_tunnel_args": "--fec --fec-profile p30",
#        "needs_inferred": False,
#    },
#]
