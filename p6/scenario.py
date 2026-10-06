#!/usr/bin/env python3
# Minimal scenario for the park/evict truncation patches: 4 requests.
#   L1 grows a long conversation (62K).
#   X1 interleaves (40K): L's chain parks (2.8G, fits budget alone).
#   Y1 interleaves again (28K): X's chain parks -> 2.8G+1.9G > 3.5G budget.
#     ON truncates L's mirror to a 32K checkpoint and keeps it;
#     OFF evicts L's mirror wholesale.
#   L2 returns to L: ON restores 32768 (~27s), OFF re-prefills 62K (~50s).
# The whole ON/OFF delta lands on the L2 line. (3 requests cannot work: the
# return request takes its mirror before parking the current chain, so the
# byte budget is never exceeded with only two chains.)
# Usage: scenario.py <tag>
import json, sys, time, urllib.request

URL = "http://192.168.10.226:60226/v1/chat/completions"
tag = sys.argv[1] if len(sys.argv) > 1 else "on"

def sent(n, seed):
    return " ".join(f"the quick brown fox jumps over the lazy dog entry {seed}{i}." for i in range(n))

def ask(messages, max_tokens=1):
    body = json.dumps({"model": "x", "messages": messages, "max_tokens": max_tokens, "temperature": 0}).encode()
    t0 = time.time()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=900))
    return time.time() - t0, r["usage"]["prompt_tokens"]

rows = []
def step(name, msgs):
    dt, pt = ask(msgs)
    rows.append((name, pt, round(dt, 1)))
    print(f"{name}: prompt={pt} wall={dt:.1f}s", flush=True)

SYS = [{"role": "system", "content": "You are a test subject. Answer with OK."}]

L1 = SYS + [{"role": "user", "content": sent(3700, 1)}]
step("L1-grow62K", L1)
step("X1-interleave40K", SYS + [{"role": "user", "content": sent(2500, 2)}])
step("Y1-interleave28K", SYS + [{"role": "user", "content": sent(1700, 3)}])
L2 = L1 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 4)}]
step("L2-return", L2)

print(f"TOTAL[{tag}] wall={round(sum(r[2] for r in rows), 1)}s", flush=True)
