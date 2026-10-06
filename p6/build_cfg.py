#!/usr/bin/env python3
# Build strata-test-on.json / strata-test-off.json from the pristine production config
# (strata-iq3_s.json.bak-live, created by run.sh on first use).
import json, os

S = os.path.expanduser("/home/hzy/code/Strata")
SRC = os.path.join(S, "strata-iq3_s.json.bak-live")
if not os.path.exists(SRC):
    SRC = os.path.join(S, "strata-iq3_s.json")
cfg = json.load(open(SRC))
args = cfg["args"]

# test knobs (both passes): small cache budget so park/evict pressure is reachable
repl = {
    "--conversation-cache-mib": "3500",
    "--conversation-cache-slots": "3",
    "--conversation-cache-min-tokens": "4096",
    "--conversation-cache-min-free-mib": "4096",
    "--prompt-cache-every": "4096",
}
out = []
i = 0
while i < len(args):
    a = args[i]
    if a in repl:
        out += [a, repl[a]]; i += 2; continue
    if a == "--prompt-cache-spread":
        i += 1; continue          # per-pass
    if a in ("--prompt-cache", "--slots"):
        i += 2; continue          # set below
    out.append(a); i += 1
args = out + ["--prompt-cache", "8", "--slots", "2"]

on = json.loads(json.dumps(cfg))
on["args"] = args + ["--prompt-cache-spread"]
on["env"] = dict(cfg.get("env", {}), STRATA_PARK_TRUNCATE="1")
off = json.loads(json.dumps(cfg))
off["args"] = args
off["env"] = dict(cfg.get("env", {}), STRATA_PARK_TRUNCATE="0")

json.dump(on, open(os.path.join(S, "strata-test-on.json"), "w"), indent=1)
json.dump(off, open(os.path.join(S, "strata-test-off.json"), "w"), indent=1)
print("built strata-test-on.json / strata-test-off.json from", SRC)
