# Two separate compatibility observations

Weekly Test checks current main source, including the fresh-dependency wheel
probe and the independent locked advisory audit. Published wheel compatibility
instead selects the current public PyPI release, binds its exact wheel filename
and SHA-256 to the index, installs those bytes with fresh admitted dependencies,
and exercises installed startup, CLI, helper resolution, rejection/no-echo,
downstream behavior and cleanup. Neither result stands in for the other.

The new lane runs weekly at 08:30 UTC Monday and on manual dispatch. It is bounded
to ten minutes, uses contents:read, and retains only version/hash/index/run and
resolved dependency metadata for fourteen days. It has no package/registry write
permission and does not publish a release. No token or private configuration is
needed. The repository owner owns triage: during the weekly repository review,
check this workflow's enabled state and latest scheduled run, including a missing
run. GitHub sends scheduled-workflow notifications to the last cron editor,
subject to that user's notification settings; this is not a maintainer-wide
notification guarantee. In public repositories, sixty days without repository
activity automatically disables schedules. Re-enable through the reviewed
repository workflow procedure and read back the state. No external messages or
new service is introduced. See [GitHub's schedule reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
A failure remains visible even when current main passes; triage it as dependency
drift, a published-product defect, or evolution of the current-main probe.
The report's source SHA distinguishes those cases.

This scheduled context is not required on unrelated PR heads and creates no merge
or asset-exemption authorization. The nonpublishing ai rehearsal of PyPI 3.0.0
failed the HTTP rejected-value no-echo oracle. Its exact wheel SHA-256 is
c95c898e3b26bec0e4edd618b8c23389bab8ba8e85d1ccf4582649aafec471cc;
the failed report preserves all 44 installed dependencies. Keep this red product
observation until a separately authorized fixed release passes. No production
release is authorized by this lane. Hosted scheduling and failure-artifact wiring
remain to be demonstrated after landing; observations have not started.

The wheel_metadata_url field records the fixed public PyPI JSON metadata endpoint.
It does not claim a dependency resolver index: uv may use runner configuration
and index environment variables. The report retains actual resolved versions,
without private index URLs or credentials.
