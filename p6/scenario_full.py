#!/usr/bin/env python3
# Combined-effect scenario for the three cache patches. Usage: scenario.py <tag>
# 1) A grows 44K (checkpoints land every 4096, chain cap 8 -> retention policy visible)
# 2) B,C overwrite the main chain: A parks (park-side truncation), C forces byte pressure
#    (eviction-side truncation)
# 3) return to A: restored depth shows the park/evict truncation ladder
# 4) fork A at 20K: mount depth shows geometric vs plain retention
# 5) A7 decodes long in a slot while D1/D2 push D onto the main chain; A8 arrives while
#    A7 is still decoding -> active-slot source (patch 3)
import json, sys, threading, time, urllib.request

URL = "http://192.168.10.226:60226/v1/chat/completions"
tag = sys.argv[1]

def sent(n, seed):
    return " ".join(f"the quick brown fox jumps over the lazy dog entry {seed}{i}." for i in range(n))

def ask(messages, max_tokens=1):
    body = json.dumps({"model": "x", "messages": messages, "max_tokens": max_tokens, "temperature": 0}).encode()
    t0 = time.time()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=900))
    return time.time() - t0, r["usage"]["prompt_tokens"]

log = []
def step(name, msgs, mt=1):
    dt, pt = ask(msgs, mt)
    log.append((name, pt, round(dt, 1)))
    print(f"{name}: prompt={pt} wall={dt:.1f}s", flush=True)
    return dt

SYS = [{"role": "system", "content": "You are a test subject. Answer with OK."}]

A1 = SYS + [{"role": "user", "content": sent(3700, 1)}]
step("A1-grow44K", A1)
A2 = A1 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 2)}]
step("A2-turn", A2)
step("B1-overwrite30K", SYS + [{"role": "user", "content": sent(2500, 3)}])
step("C1-overwrite20K", SYS + [{"role": "user", "content": sent(1700, 4)}])
A5 = A2 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 5)}]
step("A5-return", A5)
step("A6-fork20K", SYS + [{"role": "user", "content": sent(1650, 1) + " FORK QUESTION"}])
A7 = A5 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(30, 6) + chr(10)*2 + "Count from 1 to 1200, one number per line, nothing else."}]
D1 = SYS + [{"role": "user", "content": sent(2200, 7)}]

res = {}
def run(k, m, mt):
    try:
        res[k] = ask(m, mt)
    except Exception as e:
        res[k] = (0.0, 0); print(k, "failed:", e, flush=True)
th = threading.Thread(target=run, args=("A7-longdecode", A7, 2500))
th.start()
time.sleep(2)
step("D1-concurrent", D1)          # prefills while A7 decodes
D2 = D1 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(20, 8)}]
step("D2-pushmain", D2)            # main chain now holds D
A8 = A7 + [{"role": "assistant", "content": "OK"}, {"role": "user", "content": sent(20, 9)}]
step("A8-slotreturn", A8)          # A7 still decoding -> active slot is the only deep source
th.join()
log.append(("A7-longdecode", res["A7-longdecode"][1], round(res["A7-longdecode"][0], 1)))
print(f"A7-longdecode: prompt={res['A7-longdecode'][1]} wall={res['A7-longdecode'][0]:.1f}s", flush=True)

tot = sum(w for _, _, w in log)
print(f"TOTAL[{tag}] wall={tot:.1f}s")
json.dump(log, open(f"/tmp/scenario_{tag}.json", "w"))
