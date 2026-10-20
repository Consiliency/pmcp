# CI diagnostic artifacts

The Linux full suite emits JUnit locally and an opt-in plugin records setup,
call and teardown durations by worker on the controller, preserving received
phases when a worker crashes. A single bounded inventory records all workers,
the expected count, and explicit completion/crash state. `collected_total` is
the repeated collection size; `selected` counts distinct observed setup IDs per
worker and `completed` counts teardown IDs. Neither asserts that queued but
unobserved tests ran. Incomplete inventories remain marked `complete: false`
with a warning; their available phases can still be uploaded with failure JUnit.
A missing controller report fails sanitization. Collect-only discovery loads
the same plugin but never changes diagnostic files. The sanitizer reconstructs
an allowlist:
opaque hashed case IDs, outcomes, durations, a fixed version inventory, and the
run SHA/ID/attempt/event. Parameter text, assertion messages, captured streams,
properties, host paths and arbitrary metadata never enter the uploaded artifact.
The sanitizer re-derives timing IDs and reduces multiple setup/call/teardown
outcomes by severity (error, failure, skipped), without copying outcome text.
Missing JUnit durations remain missing. Environment recording runs after the
suite under `always()`, so a tool-version failure cannot suppress real tests.
JUnit and phase IDs use different source naming conventions; compare within each
format, rather than assuming they share an ID. No report is a skip or publication
credential.

`ci-reports-<run>-<attempt>-linux` expires after 14 days. Only `.ci-reports/safe/`
is uploaded, after successful sanitization. Raw files stay on the ephemeral
runner. Set `PMCP_CI_DISABLE_REPORTS` to any nonempty repository variable to stop
uploads. This does not change tests or coverage. A sanitizer failure fails the
job and suppresses upload; missing reports are visible when the runner can still
execute the reporting step. A hard runner kill may prevent that step too.

The suite selection, default non-live/non-slow exclusions, loadfile scheduling,
and combined 60% coverage threshold remain intact. `-n auto` is not a fixed
worker-count guarantee. Environment selection is Python 3.12 and uv 0.13.1 for
this lane; fresh/floor installation probes keep their independent resolution.
The reports do not establish the cause of an intermittent failure. Compare the
same source and environment and predeclare bounded experiments before changing
any timing or lifecycle oracle.

Review contract: this diagnostics slice implements accepted-plan AC-08 and the
related AC-01/06/11/13 prerequisites. Every PR still needs its exact-head
independent cross-vendor panel with a president and all finding dispositions.
Substantive review cap: three rounds; no missing seat is approval. Hosted
observation and later scope/protection/publication activation are separate work.

Source binding: accepted plan SHA-256
`eaed8eee0b5c38cc62fd27e41e04034056cb810e9ad401a2e2bf1a66309e65f7`;
original source `1e0b0b0dc98c5a94b869058d85ba9c1db9c3c94c`; integration base
`446afb18a67efcedb46c71136c9d966c2441d772` is the separate advisory dependency/floor patch, which still requires independent review. Exact implementation heads and review dispositions are
recorded in PR #425 and the durable implementation status, avoiding a
self-referential commit identifier in this document.

Sanitized report filenames include the run, attempt and worker: per-worker
`timings-<run>-<attempt>-gwN.json` files hold phases, while the `controller`
reports hold aggregate timing metadata, JUnit and environment identity. The
controller index points to each worker file without duplicating phase payloads.
A whole-process-group interrupt through uv can interrupt pytest twice during
xdist shutdown and prevent both raw reports. The subsequent fixed refusal is
missing evidence; it never establishes a successful test or complete timing.
