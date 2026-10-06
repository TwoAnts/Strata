#!/usr/bin/env python3
# p13: return the parked copy's spread checkpoints to a slot-mounted chain.
import sys, re

def edit(path, old, new, count=1, marker=None):
    s = open(path).read()
    if marker is not None and marker in s:
        print(f"already patched: {path}")
        return
    n = s.count(old)
    if n != count:
        sys.exit(f"ANCHOR FAIL {path}: found {n} of the anchor (want {count})")
    open(path, "w").write(s.replace(old, new, count))
    print(f"patched {path}")

def edit_marker(path, old, new, marker):
    edit(path, old, new, 1, marker)

HPP = "include/strata/core/conversation_cache.hpp"
GEN = "src/program/generate.cpp"
TST = "src/core/conversation_cache_test.cpp"

# ---- 1) the cache method, right after best() ----
edit_marker(HPP,
"""    SavedConversation take(size_t index) {""",
"""    /// A conversation mounted through a batch slot comes back with only the checkpoint the
    /// slot kept (typically one: its turn or endpoint).  The checkpoints spread along the chain
    /// at read time are not lost - the parked copy of the same conversation still holds them.
    /// Hand them back: from the most recently parked copy that has at least one checkpoint that
    /// is a prefix of `prompt`, copy every prefix-compatible checkpoint whose depth the chain
    /// does not already hold.  The `used` stamps travel with the copies, so the returned
    /// survivors age from their last hit and the normal retention policy thins them again.
    template<class Token>
    size_t merge_pool(const std::vector<Token>& prompt,
                      const std::vector<ConversationImageKey>& images, bool cvec,
                      std::vector<ConversationCheckpoint>& into) const {
        for (size_t i = entries_.size(); i-- > 0;) {
            const auto& e = entries_[i];
            if (e.cvec != cvec) continue;
            std::vector<const ConversationCheckpoint*> give;
            for (const auto& c : e.checkpoints)
                if (conversation_prefix(c, prompt, images) > 0) give.push_back(&c);
            if (give.empty()) continue;   // not this conversation's chain (no point of it is a prefix)
            size_t added = 0;
            for (const auto* c : give) {
                bool dup = false;
                for (const auto& k : into)
                    if (k.ids.size() == c->ids.size()) { dup = true; break; }
                if (dup) continue;
                into.push_back(*c);
                ++added;
            }
            return added;
        }
        return 0;
    }

    SavedConversation take(size_t index) {""", "size_t merge_pool")

# ---- 2) the serve hook: after the slot's own checkpoint(s) came back, before live_imgs.clear() ----
edit_marker(GEN,
"""                    } else {
                        live = bs[(size_t) slot_source].ids;
                    }
                    live_imgs.clear();
                    resume = slot_tokens;""",
"""                    } else {
                        live = bs[(size_t) slot_source].ids;
                    }
                    // a batch slot carries at most one checkpoint of its conversation: the spread the read
                    // laid along the chain survives only on the parked copy.  Hand those survivors back, or
                    // every main-chain switch that returns through a slot starves the truncate-park ladder
                    // and the next cross-slot mount down to a single point.  Text only for now: an empty
                    // image list never matches a checkpoint that carries image keys.  STRATA_POOL_RESTORE=0
                    // turns the return off.
                    static const bool pool_restore = [] {
                        const char* v = std::getenv("STRATA_POOL_RESTORE");
                        return v == nullptr || std::atoi(v) != 0;
                    }();
                    if (pool_restore && !bs[(size_t) slot_source].img) {
                        const size_t merged = conversations.merge_pool(
                            ids, std::vector<strata::core::ConversationImageKey>{},
                            bs[(size_t) slot_source].cvec, checks);
                        if (merged)
                            std::fprintf(stderr, "strata serve: conversation cache: returned %zu checkpoint(s) "
                                         "of this conversation from the parked copy to the slot-mounted chain\\n",
                                         merged);
                    }
                    live_imgs.clear();
                    resume = slot_tokens;""", "STRATA_POOL_RESTORE")

# ---- 3) tests: insert before the final return of main ----
s = open(TST).read()
m = list(re.finditer(r'^    std::printf\("conversation_cache_test: %d checks passed', s, re.M))
if len(m) != 1:
    sys.exit(f"ANCHOR FAIL {TST}: {len(m)} final-print anchors")
test = """    // merge_pool: the parked copy's spread checkpoints return to a slot-mounted chain
    {
        auto cp = [](std::vector<int32_t> ids) { ConversationCheckpoint c; c.ids = std::move(ids); return c; };
        ConversationCache cache(1 << 20, 4);
        SavedConversation e;
        e.live = cp({1, 2, 3, 4, 5, 6, 7, 8});
        e.checkpoints = {cp({1, 2, 3, 4}), cp({1, 2, 3, 4, 5, 6})};
        check(cache.put(std::move(e)), "merge_pool: the spread copy parks");
        std::vector<ConversationCheckpoint> into = {cp({1, 2, 3, 4, 5, 6, 7, 8})};   // the slot's one point
        const std::vector<int32_t> prompt = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
        check(cache.merge_pool(prompt, {}, true, into) == 2, "merge_pool: both spread points return");
        check(cache.merge_pool(prompt, {}, true, into) == 0, "merge_pool: depths already held are not duplicated");
        const std::vector<int32_t> diverged = {1, 2, 3, 99};
        std::vector<ConversationCheckpoint> other = {cp({1, 2, 50})};
        check(cache.merge_pool(diverged, {}, true, other) == 0, "merge_pool: a different chain merges nothing");
        check(into.size() == 3, "merge_pool: the chain ends up with the full pool");
    }

"""
open(TST, "w").write(s[:m[0].start()] + test + s[m[0].start():])
print(f"patched {TST}")
