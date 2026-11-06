"""Did the council DECLINE a request (a model refusal), as opposed to being unable, unsure or refused by the permission layer?
Deterministic phrase rules on the final answer, no model call. Honest inability ("I can't find that in the vault", "I cannot verify") is NOT a decline.
A decline is what makes the Rogue team available: the UI then offers it, and the server re-checks the stored offer before it will run."""
import re

_APOS = "['’]"
DECLINE = [
    # "I can't help/assist/comply/do that": a refusal of the request itself
    rf"\bI (?:can{_APOS}?t|cannot|can not|won{_APOS}?t|will not|am not able to|am unable to|{_APOS}m not able to|{_APOS}m unable to|must decline to|have to decline to|{_APOS}m going to have to decline)\s+(?:help|assist|comply|fulfil+|fulfill|do that|do this|support (?:that|this|you with)|walk you through (?:that|this))",
    # "I can't provide THAT/instructions/guidance": a refusal, unlike "I can't provide the details because the page did not load"
    rf"\bI (?:can{_APOS}?t|cannot|can not|won{_APOS}?t|will not|am not able to|am unable to|{_APOS}m not able to|{_APOS}m unable to)\s+(?:provide|give you|share|write|create|generate|explain how)\s+(?:that|this|those|these|any|instructions|guidance|assistance|help|a (?:guide|walkthrough|tutorial|method|step)|step[- ]by[- ]step|information (?:on|about|that could))",
    rf"\b(?:I{_APOS}m|I am) sorry,? (?:but )?I (?:can{_APOS}?t|cannot|won{_APOS}?t|am unable to (?:help|assist)|{_APOS}m unable to (?:help|assist))",
    r"\bI (?:must|have to|need to) (?:decline|refuse)\b",
    r"\bagainst (?:my|our|the) (?:guidelines|policy|policies|programming|principles|rules|safety)",
    r"\b(?:this|that) (?:request )?(?:violates|goes against|conflicts with) (?:my|our|the)",
    rf"\bI (?:don{_APOS}?t|do not) feel comfortable (?:helping|providing|assisting|doing)",
    r"\bI(?:'|\u2019)?m not (?:going to|able to) (?:help|assist|provide) with (?:that|this)",
    rf"\bas an AI(?: language model| assistant)?,? I (?:can{_APOS}?t|cannot|am unable|{_APOS}m unable|won{_APOS}?t) (?:help|assist|provide|do|comply)",
    r"\bnot something I can (?:help|assist) with\b",
]
_RX = [re.compile(p, re.I) for p in DECLINE]
PERMISSION = "refused by the council's permission layer"        # an authority limit, not a content refusal: Rogue does not override it


def detect(answer):
    """-> matched snippet (str) or None. Looks at short answers only: a long, substantive answer that happens to say 'I can't' is not a decline."""
    a = (answer or "").strip()
    if not a or len(a) > 1500: return None
    head = a[:600]
    if PERMISSION in a.lower(): return None
    for rx in _RX:
        m = rx.search(head)
        if m: return head[max(0, m.start() - 10): m.end() + 50].replace("\n", " ")
    return None
