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
needed. Results belong to the repository maintainers through the ordinary GitHub
Actions result/notification controls; no external messages or new service is
introduced. A failure remains visible even when current main passes.

This scheduled context is not required on unrelated PR heads and creates no merge
or asset-exemption authorization. The first real published-wheel rehearsal and
scheduled observations remain implementation evidence, separate from code review.
