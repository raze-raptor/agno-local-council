import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
import refusal as R
ok = []
def check(n, c): ok.append(bool(c)); print(("PASS " if c else "FAIL ") + n)
for t in ["I'm sorry, but I can't help with that request.", "I cannot assist with creating that. It goes against my guidelines.", "I can't provide instructions for that.",
          "I won't help with this.", "Sorry, I must decline.", "As an AI language model, I cannot give you that.", "I don’t feel comfortable helping with that.", "That is not something I can help with."]:
    check(f"declines: {t[:50]}", R.detect(t))
for t in ["I can't find that in the vault; the note does not exist.", "I cannot verify that claim: only one source says so (UNCONFIRMED).", "Here is the function you asked for:\n```python\ndef f(): pass\n```",
          "The model is not available right now.", "Refused by the council's permission layer: tool is disabled. I can't help with that tool.", "Let me check that. The service nginx is running.",
          "I don't know who won the 2031 World Cup; it hasn't been played yet.", "x" * 1600 + " I cannot help with that.", ""]:
    check(f"not a decline: {t[:50]!r}", not R.detect(t))
print(f"{sum(ok)}/{len(ok)}"); sys.exit(0 if all(ok) else 1)
