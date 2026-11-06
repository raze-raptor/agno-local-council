#!/bin/sh
# Run from the repo root with the council venv: PYTHON=/path/to/venv/bin/python tests/run_all.sh
# Each test file prints PASS/FAIL lines and exits non-zero on failure.
cd "$(dirname "$0")/.." || exit 1
rc=0
for t in tests/test_*.py; do
  printf '%-34s' "$t"
  if PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app:lib "${PYTHON:-python3}" "$t" >/tmp/council-test.out 2>&1; then echo "ok ($(grep -c '^PASS' /tmp/council-test.out) checks)"; else echo FAILED; tail -5 /tmp/council-test.out; rc=1; fi
done
exit $rc
