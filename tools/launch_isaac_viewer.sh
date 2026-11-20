#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' 'Usage: ACCEPT_EULA=Y tools/launch_isaac_viewer.sh [HOST_IPV4]' \
        '       tools/launch_isaac_viewer.sh --stop' \
        'Run on the NVIDIA Linux host. Connect using the Isaac Sim WebRTC Streaming Client.'
}

case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --stop) action=stop; shift ;;
    *) action=start ;;
esac
if [[ "$#" -gt 1 ]]; then
    usage >&2
    exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export HOTEL_STREAM_IP="${1:-$(hostname -I | awk '{print $1}')}"
compose=(docker compose -p hotel-isaac-viewer -f compose.yaml -f compose.isaac-viewer.yaml)
if [[ "$action" == stop ]]; then
    "${compose[@]}" down
    exit
fi
if ! ip -o -4 addr show | awk -v address="$HOTEL_STREAM_IP" '
    { split($4, subnet, "/"); if (subnet[1] == address) found = 1 }
    END { exit !found }
'; then
    printf '%s\n' 'Choose an IPv4 address assigned to this host and reachable by the viewing computer.' >&2
    exit 2
fi

"${compose[@]}" up --build -d
printf '\nConnect the Isaac Sim WebRTC Streaming Client to %s.\n' "$HOTEL_STREAM_IP"
printf 'The task dashboard listens on http://127.0.0.1:%s/.\n' "${HOTEL_VIEWER_PORT:-8788}"
