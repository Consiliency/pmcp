# CI diagnostic artifacts

The Linux full suite emits JUnit locally and an opt-in plugin records setup,
call and teardown durations by worker. The sanitizer reconstructs an allowlist:
opaque hashed case IDs, outcomes, durations, a fixed version inventory, and the
run SHA/ID/attempt/event. Parameter text, assertion messages, captured streams,
properties, host paths and arbitrary metadata never enter the uploaded artifact.
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
