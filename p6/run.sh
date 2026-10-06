#!/usr/bin/env bash
# Strata cache-patch ON/OFF scenario runner.
# Usage:  bash ~/code/Strata/p6/run.sh [on|off|both]     (default: both)
# Each pass is self-contained: swap test config -> restart -> run scenario -> restore live config.
# Outputs: p6/out_<tag>.txt (client timings), p6/log_<tag>.txt (engine log slice).
# Manual equivalent:
#   cp ~/code/Strata/strata-test-on.json ~/code/Strata/strata-iq3_s.json
#   sudo systemctl reset-failed strata && sudo systemctl restart strata
#   (wait for: curl -s http://127.0.0.1:60226/health)
#   python3 ~/code/Strata/p6/scenario.py on
#   cp ~/code/Strata/strata-iq3_s.json.bak-live ~/code/Strata/strata-iq3_s.json
#   sudo systemctl reset-failed strata && sudo systemctl restart strata
set -u
D=$(cd "$(dirname "$0")" && pwd)
S=$D/..                       # ~/code/Strata
LIVE=$S/strata-iq3_s.json
BAK=$S/strata-iq3_s.json.bak-live
ENGLOG=$S/strata-iq3_s.log

mode=${1:-both}

# keep a pristine copy of the production config
[ -f "$BAK" ] || { cp "$LIVE" "$BAK"; echo "saved $BAK"; }

wait_health () {
  for i in $(seq 1 36); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:60226/health)
    [ "$code" = 200 ] && return 0
    sleep 5
  done
  echo "health=$code after 180s -- ABORT"; return 1
}

run_pass () {
  local tag=$1
  echo "== pass $tag: swapping config"
  cp "$S/strata-test-$tag.json" "$LIVE"
  local L0; L0=$(wc -l < "$ENGLOG")
  sudo systemctl reset-failed strata
  sudo systemctl restart strata
  wait_health || { cp "$BAK" "$LIVE"; sudo systemctl reset-failed strata; sudo systemctl restart strata; return 1; }
  python3 "$D/scenario.py" "$tag" 2>&1 | tee "$D/out_$tag.txt"
  tail -n +$((L0+1)) "$ENGLOG" > "$D/log_$tag.txt"
  echo "== pass $tag: restoring live config"
  cp "$BAK" "$LIVE"
  sudo systemctl reset-failed strata
  sudo systemctl restart strata
  wait_health
}

python3 "$D/build_cfg.py"
case "$mode" in
  on)   run_pass on ;;
  off)  run_pass off ;;
  both) run_pass on; run_pass off ;;
  *)    echo "usage: run.sh [on|off|both]"; exit 2 ;;
esac
systemctl is-active strata
echo "done. results: $D/out_*.txt $D/log_*.txt"
