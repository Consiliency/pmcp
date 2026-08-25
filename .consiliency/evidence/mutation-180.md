# Mutation evidence — Consiliency/pmcp#180

Every proof runs **one test node alone** against the mutant, never the class.
A class-level run reddens on any member, so it cannot show that the specific
test is load-bearing — the hole through which two hollow tests shipped in this
repo's last two phases (board finding, adversarial + red-team seats).

## 1. docker branch reverted to `raw.split(":")[0]`

    FAILED ...::TestPackageIdentityCollisions::test_docker_registry_host_port_distinguishes_images
    1 failed in 0.08s

## 2. npm subcommand skip disabled (`skip_subcommand = False`)

    FAILED ...::TestPackageIdentityCollisions::test_npm_exec_distinguishes_packages
    1 failed in 0.10s
    FAILED ...::TestPackageIdentityCollisions::test_npm_x_alias_distinguishes_packages
    1 failed in 0.07s

## 3. skip fires repeatedly (docker-style anywhere-loop)

    FAILED ...::TestNpmSubcommandSkipFiresOnce::test_npm_install_of_a_package_named_i
    1 failed in 0.09s
    FAILED ...::TestNpmSubcommandSkipFiresOnce::test_npm_exec_of_a_package_named_exec
    1 failed in 0.09s

## 4. digest NOT stripped first (the first draft's rule)

    FAILED ...::TestDockerReferenceSplitting::test_name_and_tag_agree[img@sha256:abc-img-None]
    FAILED ...::TestDockerReferenceSplitting::test_name_and_tag_agree[registry:5000/img@sha256:abc-registry:5000/img-None]
    2 failed, 8 passed in 0.11s

Note this mutant fails **only** the digest cases and leaves the other eight
green — the parametrised ids name exactly which forms the ordering protects.

## 5. pin-detection call site reverted to `_npm_package_arg(args, "npx")`

    FAILED ...::TestUpdateServerVersionRepair::test_detect_effective_version_pin_matrix
    1 failed, 185 deselected in 0.95s

## 6. `_docker_image_tag` stops stripping the digest

    FAILED ...::TestUpdateServerVersionRepair::test_detect_effective_version_pin_matrix
    1 failed, 185 deselected in 1.02s

Mutants 5 and 6 are what pin the two helpers as complements: each half of the
pair has its own mutant, so they cannot drift apart silently.

## 7. pin check ignores the digest (the regression this PR introduced and then fixed)

Removing the `_docker_image_digest` consultation from
`_detect_effective_version_pin`:

    FAILED ...::TestUpdateServerVersionRepair::test_detect_effective_version_pin_matrix
    1 failed, 185 deselected in 1.11s

This mutant is the defect the red-team seat found in the first implementation.
Stripping `@digest` before the tag scan was correct for the NAME, but reading
the tag alone then reported a digest-only reference as unpinned -- so a
digest-pinned server, the tightest pin docker has, read as unpinned and
`update_server` would have pulled `image:latest`, restarted the unchanged
config, and recorded the registry's newest digest as though it had updated.

Before this PR the same reference produced the garbage pin `'abc'`, which was
wrong but at least truthy, so the refusal still fired. The first draft of the
fix turned a wrong-but-safe value into a right-looking-but-unsafe `None` --
a reminder that "more correct in isolation" is not the same as "safe in
composition".

## 8. flag-skip and subcommand check swapped (adversarial seat's surviving mutant)

    FAILED ...::TestNpmSubcommandSkipFiresOnce::test_a_leading_flag_does_not_consume_the_subcommand_skip
    1 failed in 0.08s

The code was already correct here -- `npm --silent exec old-pkg` resolved to
`old-pkg`. What was missing was any test that PINNED the ordering: swapping the
two checks passed all 317 tests, because every other npm test puts the
subcommand first. The one-shot skip must fire on the first non-flag token, not
the first argv token, or `--silent` consumes it and `exec` is read as the
package.

A surviving mutant, not a live defect -- and exactly the thing worth asking a
board for, since a passing suite cannot distinguish "correct and pinned" from
"correct by accident".

## 9. pin call site hardcoded to "npm" (correctness seat's mutant B)

    FAILED ...::TestUpdateServerVersionRepair::test_detect_effective_version_pin_matrix
    1 failed, 185 deselected in 1.54s

This mutant is the mirror of proof 5 (which hardcodes `"npx"`), and it
survived until now because the guard written to catch it was **hollow**. That
guard was `detect("npm","npx",["-y","exec@1.2"]) == "1.2"`, commented "npx
still must not skip a package genuinely named exec" -- but the skip matches the
RAW token against `_NPM_SUBCOMMANDS`, and `exec@1.2` is not in that set. So the
mutant returns the identical `1.2` and the assertion passes under exactly the
condition it claimed to forbid. Measured:

    correct : npx -y exec@1.2     -> '1.2'
    mutant  : npx -y exec@1.2     -> '1.2'    <- no divergence, guard useless
    correct : npx -y exec pkg@1.2 -> None
    mutant  : npx -y exec pkg@1.2 -> '1.2'    <- divergence

The bare `exec` token is what discriminates. Fixed by adding
`assert detect("npm","npx",["-y","exec","pkg@1.2"]) is None`.

Worth stating plainly: this is the third hollow test in three phases, and the
second one written by the agent that had just documented the failure mode. A
hollow assertion is indistinguishable from a real one when read; only mutating
the exact line it claims to pin tells them apart.

## Tooling note: stale bytecode can fabricate BOTH false red and false green

A pure-reorder mutant has identical byte size, and apply/compile/restore inside
one second defeats `.pyc` invalidation (whole-second mtime + size). A full-suite
run reported proof 8's test as FAILING against correct source; purging
`src/**/__pycache__` and re-running gave 5 passed. The same mechanism can
fabricate a false GREEN -- i.e. certify a mutant as killed when it was not.

Every proof above was re-confirmed with `__pycache__` purged and
`PYTHONDONTWRITEBYTECODE=1`. Any future mutation proof in this repo must do the
same (correctness seat's incident disclosure).
