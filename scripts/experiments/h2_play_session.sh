#!/bin/bash
# Record a play session of the dev xemu (texture-pack build): screen video, xemu's log and
# one timestamp per guest frame (frames.txt), all in a new ~/RRL-Workspace/sessions/<timestamp>/.
# Recording stops when xemu quits. HDD writes go to a throwaway snapshot (-snapshot).
#
# Usage: scripts/experiments/h2_play_session.sh [pack_dir]   (no pack_dir: original textures)
set -euo pipefail

XEMU=~/Applications/xemu-vulkan-dev.app/Contents/MacOS/xemu
TOML=~/Library/Application\ Support/xemu/xemu/xemu.toml
out=~/RRL-Workspace/sessions/$(date +%Y%m%d-%H%M%S)
mkdir -p "$out"
cp "$TOML" "$out/xemu.toml.backup"
echo "pack: ${1:-none}" > "$out/session.txt"

export XEMU_FRAME_LOG="$out/frames.txt"
if [ -n "${1:-}" ]; then export XEMU_TEXTURE_PACK_DIR="$1"; fi

# Half-resolution HEVC on the hardware encoder keeps the cost to the game low.
ffmpeg -hide_banner -loglevel error -f avfoundation -capture_cursor 0 -framerate 30 \
    -pixel_format nv12 -i "Capture screen 0:none" -vf scale=iw/2:-2 -c:v hevc_videotoolbox -b:v 8M \
    -tag:v hvc1 "$out/screen.mp4" < /dev/null &
rec=$!

"$XEMU" -snapshot > "$out/xemu.log" 2>&1 || true

kill -INT "$rec" 2>/dev/null || true  # SIGINT lets ffmpeg finish the mp4 index
wait "$rec" 2>/dev/null || true
cmp -s "$TOML" "$out/xemu.toml.backup" || echo "warning: xemu.toml changed" | tee -a "$out/session.txt"
python3 - "$out/frames.txt" <<'PY' | tee -a "$out/session.txt"
import sys
t = [int(x) for x in open(sys.argv[1])]
d = sorted((b - a) / 1000 for a, b in zip(t, t[1:]))
if d:
    pct = lambda q: d[min(len(d) - 1, int(q * len(d)))]
    print(f"{len(t)} frames in {(t[-1] - t[0]) / 1e6:.0f} s: median {pct(.5):.1f} ms, "
          f"p99 {pct(.99):.1f} ms, worst {d[-1]:.0f} ms, hitches >100 ms: {sum(x > 100 for x in d)}")
PY
echo "session saved in $out"
