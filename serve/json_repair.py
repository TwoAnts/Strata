"""serve/json_repair.py - local patch: ask the model to fix a structured answer that failed validation.

The native engine has no grammar decoder, so a json_object answer can break mid-JSON (a missing comma,
an unterminated string). Upstream answers 502 in that case; this patch (opt-in, config "json_repair":
1-3) shows the model its own broken answer and asks for a corrected one, up to that many times, before
falling back to the 502. Non-streaming /v1/chat/completions only: a stream's headers are already out.

Kept separate from server.py so an upstream update only needs the small call-site patch to survive.
"""
from __future__ import annotations

NOTE = ("Your previous answer failed validation: {reason}\n"
        "Output ONLY the corrected, complete JSON object: no explanation, no code fences.")


def repair_structured(svc, req, messages, kw, validator, first_error, bad_text, attempts):
    """Regenerate with feedback until the answer validates; None = give up (the caller re-raises).

    messages/kw/validator are the prepared ones from the failed pass. The engine call mirrors
    _openai's own path (prepare -> openai_chunks -> openai_collect) without tools: a structured
    response_format already rejects tools, so none can be set here.
    """
    import threading
    from serve.server import openai_chunks, openai_collect, validated_json

    attempts = max(0, min(int(attempts), 3))
    last_error = first_error
    for _ in range(attempts):
        round_msgs = list(messages)
        if bad_text:
            round_msgs.append({"role": "assistant", "content": bad_text})
        round_msgs.append({"role": "user", "content": NOTE.format(reason=str(last_error))})
        max_new = int(req.get("max_completion_tokens") or req.get("max_tokens") or 0)
        ids, thinking, max_new = svc.prepare(round_msgs, None, kw, max_new, req=req)
        cancel = threading.Event()
        result = openai_collect(openai_chunks(svc, req, ids, thinking, None, max_new, cancel))
        choice = result["choices"][0]
        try:
            content = validated_json(choice["message"]["content"], validator, choice["finish_reason"])
        except Exception as e:                       # another bad answer: feed this one back too
            bad_text, last_error = choice["message"]["content"] or "", e
            continue
        choice["message"]["content"] = content
        return result
    return None
