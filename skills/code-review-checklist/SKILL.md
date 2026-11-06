---
name: code-review-checklist
description: "Step-by-step checklist for reviewing code or a change: correctness, security, edge cases, tests. Use when asked to review, critique or check code."
---
# Code review checklist
1. State what the code is supposed to do in one sentence. If you cannot, ask for the missing context.
2. Correctness: trace one normal input and one edge input (empty, huge, wrong type, concurrent) through the code by hand.
3. Security: untrusted input reaching a shell, file path, SQL, HTML or URL; secrets in code or logs; missing auth or permission checks.
4. Failure paths: what happens when the network, disk or a dependency fails? Is data lost or left half-written?
5. Tests: is there a test that would fail if the main logic broke? Name the missing one.
6. Report: problems first, most serious first, each with the line or function and a concrete fix. Then what is sound. Say plainly if nothing is wrong. Never invent a problem to look thorough.
