# SAT2 Relay 2.2.0 Test Report

Executed in the Linux build environment on the packaged source tree.

```text
Daemon tests: 37 passed
Extension tests: 3 passed
Python compileall: passed
Wheel build: passed
```

Covered areas include:

- Delivery-token-bound Decision submission.
- Role/decision mismatch rejection.
- Idempotent repeated assistant message handling.
- Deterministic Relay-generated control event comments.
- Worker checkpoint composition and Mentor routing.
- Mentor accepted human-confirmation gate.
- Outbox and dashboard visibility.
- Extension Manifest V3 load and Decision detection/manual-submit paths.

Not executed here:

- Real Windows installation smoke.
- Live ChatGPT DOM delivery in the user's browser.
- Live GitHub publication using the user's local DPAPI PAT.
- SAT2 scientific source execution, qualification, formal experiment, merge, or evidence update.
```
