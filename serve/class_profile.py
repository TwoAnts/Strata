"""serve/class_profile.py: a traffic-class profile the cache decisions can trust.

Groups finished requests into conversation "families" by the shape of their prompt head - the
first 64 token ids, with numeric-looking tokens (dates, counters) masked so a daily-report
chain and yesterday's match the same family. For each family it tracks an EWMA of how much
of the prompt the engine already had (reused / total), which is exactly the signal a later
phase needs to decide what is worth parking and what is throwaway.

With STRATA_CLASS_PROFILE_PATH set the table is remembered across restarts: a bounded
write-behind (one atomic tmp+replace an hour after startup, then at most one per day, a few KB) and a load
at startup that drops families idle for more than LOAD_MAX_IDLE. Without the env it stays
purely in-memory and every family relearns from live traffic. The table is capped (default
128 families): when it fills, the family with the weakest score times the longest idle is
replaced.

Built when STRATA_CLASS_POLICY>=1 (1 watch + refuse to park dead classes, 2 additionally
sacrifices them first when the cache is full). With the switch at 0 the server never
builds this object and the request path pays nothing.
"""
import json
import os
import re
import threading
import time

ALPHA = 0.2                  # EWMA weight of the newest sample
HEAD = 64                    # token ids of the prompt head that identify a family
MIN_LCP = 24                 # shorter heads must match this many tokens (of the shared prefix)...
MIN_RATIO = 0.85             # ...and at least this share of the shorter head to be one family
DEFAULT_CAP = 128            # max families kept; STRATA_CLASS_PROFILE_MAX overrides
SNAPSHOT = 25                # families reported by snapshot(), best score first
DEAD_N = 5                   # a family needs this many samples before it can be called dead (cls=0)
DEAD_EWMA = 0.05             # ...and this little reuse to be called dead
WARM_EWMA = 0.5              # this much reuse calls it proven (cls=2)
FIRST_SAVE = 3600            # first write-behind this long after startup (the table converges in minutes)
SAVE_EVERY = 86400           # ...then at most one save per day; the profile is not worth frequent IO
LOAD_MAX_IDLE = 14 * 86400   # a saved family idle longer than this does not come back

_MASK = re.compile(r"[\d\s.,/:_+-]+")   # tokens decoding to digits/whitespace/punctuation only:
# dates, counters and spacing are noise inside an otherwise stable system prompt


def _looks_noise(text):
    return bool(_MASK.fullmatch(text or ""))


class _Family:
    __slots__ = ("ref", "n", "reuse_ewm", "avg_total", "last_seen", "label")

    def __init__(self, ref, total, reused, label):
        self.ref = ref                    # masked token-id prefix, the family anchor
        self.n = 1
        self.reuse_ewm = float(reused) / total if total else 0.0
        self.avg_total = float(total)
        self.last_seen = time.time()
        self.label = label                # decoded head for humans, taken once

    def update(self, total, reused):
        self.n += 1
        r = float(reused) / total if total else 0.0
        self.reuse_ewm += ALPHA * (r - self.reuse_ewm)
        self.avg_total += (total - self.avg_total) / self.n
        self.last_seen = time.time()

    def score(self, now):
        # a family worth caching: reused often and lately; the log term keeps a plain
        # one-shot chain from outscoring nothing, the idle term ages stale families out
        return self.reuse_ewm + 0.15 * (self.n ** 0.5) / 4.0 - min(0.4, (now - self.last_seen) / 86400.0 * 0.4)


def fingerprint(ids, decode, head=HEAD):
    """Masked token-id prefix: numeric-looking tokens become 0 (wildcards), so date and
    counter drift inside an otherwise stable system prompt keeps the family stable."""
    fp = []
    for t in ids[:head]:
        try:
            fp.append(0 if _looks_noise(decode([t])) else int(t))
        except Exception:
            fp.append(int(t))
    return fp


class ClassProfile:
    """The family table. record() runs per finished request; snapshot() feeds GET /metrics."""

    def __init__(self, decode, cap=DEFAULT_CAP, now=None, path=None):
        self.decode = decode
        self.cap = max(8, int(cap))
        self.lock = threading.Lock()
        self.families = []
        self.path = str(path or "").strip()
        self._dirty = False
        self._next_save = time.time() + FIRST_SAVE
        if self.path:
            self._load()

    def _match(self, fp, now):
        best, best_len = None, 0
        for fam in self.families:
            i = 0
            n = min(len(fp), len(fam.ref))
            while i < n and (fp[i] == fam.ref[i] or fp[i] == 0 or fam.ref[i] == 0):
                i += 1
            if i >= MIN_LCP and i >= MIN_RATIO * n and i > best_len:
                best, best_len = fam, i
        return best

    def record(self, ids, reused, total):
        """One finished request: its prompt head, what the engine already had, prompt size."""
        if not ids or not total:
            return
        fp = fingerprint(ids, self.decode)
        with self.lock:
            now = time.time()
            fam = self._match(fp, now)
            if fam is None:
                if len(self.families) >= self.cap:   # replace the weakest, oldest family
                    worst = min(self.families, key=lambda f: f.score(now))
                    self.families.remove(worst)
                try:
                    label = self.decode(ids[:40])[:120]
                except Exception:
                    label = ""
                fam = _Family(fp, total, reused or 0, label)
                self.families.append(fam)
            else:
                fam.update(total, reused or 0)
            if self.path:
                self._dirty = True
                if now >= self._next_save:
                    self._save(now)      # a few KB: an hour after startup, then daily

    def save(self):
        """Force the write-behind now (a caller shutting down cleanly calls this)."""
        with self.lock:
            self._save(time.time())

    def _save(self, now):
        # atomic: tmp + replace, so a crash mid-write leaves the previous good file.
        # failures are swallowed: the profile is an optimisation, never a dependency.
        try:
            rows = [{"ref": f.ref, "n": f.n, "reuse_ewm": round(f.reuse_ewm, 4),
                     "avg_total": round(f.avg_total, 1), "last_seen": f.last_seen,
                     "label": f.label} for f in self.families]
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"v": 1, "saved": now, "families": rows}, fh)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass
        self._next_save = now + SAVE_EVERY

    def _load(self):
        try:
            with open(self.path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        now = time.time()
        fams = []
        for d in data.get("families", []):
            try:
                fam = _Family.__new__(_Family)
                fam.ref = [int(t) for t in d["ref"]]
                fam.n = max(1, int(d["n"]))
                fam.reuse_ewm = float(d["reuse_ewm"])
                fam.avg_total = float(d["avg_total"])
                fam.last_seen = float(d["last_seen"])
                fam.label = str(d.get("label", ""))[:120]
            except (KeyError, TypeError, ValueError):
                continue
            if fam.ref and now - fam.last_seen <= LOAD_MAX_IDLE:
                fams.append(fam)
        fams.sort(key=lambda f: -f.score(now))     # the cap keeps the strong, drops the weak
        self.families = fams[:self.cap]

    def priority(self, ids):
        """The one number a request travels to the engine with as cls=N: 0 = established one-shot,
        1 = unknown or growing, 2 = proven reuse.  A cold table never says 0 - the first visit of every
        family parks as before; the profile can only ever refuse chains that proved they never return."""
        if not ids:
            return 1
        fp = fingerprint(ids, self.decode)
        with self.lock:
            fam = self._match(fp, time.time())
        if fam is None:
            return 1
        if fam.n >= DEAD_N and fam.reuse_ewm < DEAD_EWMA:
            return 0
        return 2 if fam.reuse_ewm >= WARM_EWMA else 1

    def snapshot(self):
        with self.lock:
            now = time.time()
            rows = sorted(self.families, key=lambda f: -f.score(now))[:SNAPSHOT]
            return [{"head": f.label, "n": f.n, "reuse_ewm": round(f.reuse_ewm, 3),
                     "avg_prompt": int(f.avg_total), "idle_s": int(now - f.last_seen),
                     "score": round(f.score(now), 3)} for f in rows]
