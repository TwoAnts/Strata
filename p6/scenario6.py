#!/usr/bin/env python3
# Minimal production-shape scenario for the park/evict truncation patches.
# One long conversation, two interleaved conversations push it out of the main
# chain and create byte pressure on its parked mirror, then return to it.
# The ON/OFF delta concentrates on L3-return: ON restores the truncated mirror
# (~32K tokens), OFF lost it to wholesale eviction and re-prefills everything.
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
L2 = L1 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 2)}]
step("L2-turn", L2)
step("X1-interleave40K", SYS + [{"role": "user", "content": sent(2500, 3)}])
step("Y1-interleave28K", SYS + [{"role": "user", "content": sent(1700, 4)}])
L3 = L2 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 5)}]
step("L3-return", L3)
L4 = L3 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 6)}]
step("L4-turn", L4)

print(f"TOTAL[{tag}] wall={round(sum(r[2] for r in rows), 1)}s", flush=True)
