"""Per-NODE mutation proof for Consiliency/pmcp#182.

Each mutation reverts exactly ONE branch's rule and runs exactly ONE test node.
A red CLASS proves nothing about a specific test -- that is how hollow tests
have shipped in this repo before -- so nothing here runs a whole class.

Runs against a `cp -r` copy so the worktree is never edited (a `git checkout --`
restore has wiped uncommitted work here twice). Bytecode is purged before every
run and writing is disabled, since stale bytecode has fabricated both a false
RED and can equally fabricate a false GREEN.
"""

import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
MUT = pathlib.Path(__file__).parent / "mutcopy"
VC = MUT / "src/pmcp/manifest/version_checker.py"
HD = MUT / "src/pmcp/tools/handlers.py"

# (label, file, kind, payload, test node)
#   kind "append": append a line rebinding a module-level table
#   kind "replace": exact (old, new) source substitution
MUTATIONS = [
    (
        "uvx --python reclassified value -> boolean",
        VC, "append",
        '_UVX_VALUE_FLAGS = _UVX_VALUE_FLAGS - {"--python"}\n'
        '_UVX_BOOLEAN_FLAGS = _UVX_BOOLEAN_FLAGS | {"--python"}\n',
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_uvx_python_version_is_not_the_package",
    ),
    (
        "uvx --with reclassified value -> boolean",
        VC, "append",
        '_UVX_VALUE_FLAGS = _UVX_VALUE_FLAGS - {"--with"}\n'
        '_UVX_BOOLEAN_FLAGS = _UVX_BOOLEAN_FLAGS | {"--with"}\n',
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_uvx_with_dependency_is_not_the_package",
    ),
    (
        "pip --index-url reclassified value -> boolean",
        VC, "append",
        '_PIP_VALUE_FLAGS = _PIP_VALUE_FLAGS - {"--index-url"}\n'
        '_PIP_BOOLEAN_FLAGS = _PIP_BOOLEAN_FLAGS | {"--index-url"}\n',
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_pip_index_url_is_not_the_package",
    ),
    (
        "cargo --features reclassified value -> boolean",
        VC, "append",
        '_CARGO_VALUE_FLAGS = _CARGO_VALUE_FLAGS - {"--features"}\n'
        '_CARGO_BOOLEAN_FLAGS = _CARGO_BOOLEAN_FLAGS | {"--features"}\n',
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_cargo_features_is_not_the_crate",
    ),
    (
        "docker --env-file reclassified value -> boolean",
        VC, "append",
        '_DOCKER_VALUE_FLAGS = _DOCKER_VALUE_FLAGS - {"--env-file"}\n'
        '_DOCKER_BOOLEAN_FLAGS = _DOCKER_BOOLEAN_FLAGS | {"--env-file"}\n',
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_docker_env_file_is_not_the_image",
    ),
    (
        "docker --mount reclassified value -> boolean",
        VC, "append",
        '_DOCKER_VALUE_FLAGS = _DOCKER_VALUE_FLAGS - {"--mount"}\n'
        '_DOCKER_BOOLEAN_FLAGS = _DOCKER_BOOLEAN_FLAGS | {"--mount"}\n',
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_docker_mount_spec_is_not_the_image",
    ),
    (
        "npm --package no longer known-positive (pre-fix skip)",
        VC, "append",
        "_NPM_POSITIVE_FLAGS = frozenset()\n",
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_npm_exec_package_flag_names_the_package",
    ),
    (
        "npm repeated --package no longer refuses (returns the first)",
        VC, "replace",
        ("        return packages[0] if len(set(packages)) == 1 else None\n",
         "        return packages[0]\n"),
        "tests/test_version_checker.py::TestValueFlagCollisions"
        "::test_npm_repeated_package_flags_refuse",
    ),
    (
        "fail-closed default removed (unlisted flag skipped again)",
        VC, "replace",
        ('    return arg.startswith("-") and arg != "-" and "=" not in arg\n',
         "    return False\n"),
        "tests/test_version_checker.py::TestValueFlagsFailClosed"
        "::test_uvx_unlisted_flag_refuses",
    ),
    (
        "fail-closed default removed -- docker arm",
        VC, "replace",
        ('    return arg.startswith("-") and arg != "-" and "=" not in arg\n',
         "    return False\n"),
        "tests/test_version_checker.py::TestValueFlagsFailClosed"
        "::test_docker_unlisted_flag_refuses",
    ),
    (
        "fail-closed default removed -- update_server no-probe consequence",
        VC, "replace",
        ('    return arg.startswith("-") and arg != "-" and "=" not in arg\n',
         "    return False\n"),
        "tests/test_tools.py::TestUpdateServerVersionRepair"
        "::test_update_server_never_probes_an_unclassifiable_flag_form",
    ),
    (
        "`--flag=value` no longer treated as self-delimiting",
        VC, "replace",
        ('    return arg.startswith("-") and arg != "-" and "=" not in arg\n',
         '    return arg.startswith("-") and arg != "-"\n'),
        "tests/test_version_checker.py::TestValueFlagsFailClosed"
        "::test_unlisted_flag_with_attached_value_is_self_delimiting",
    ),
    # NOTE: the explicit `--` terminator is deliberately NOT mutated here.
    # Measured: deleting it changes no observable result, because `--` also
    # trips the fail-closed default. It is redundant-but-documenting, and both
    # the code comment and the test docstring say so rather than implying a
    # proof that does not exist.
    (
        "PEP 508 normalization removed",
        VC, "replace",
        ("    return match.group(1) if match else requirement\n",
         "    return requirement\n"),
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_from_value_is_normalized_to_its_pep508_base_name",
    ),
    (
        "PEP 508 applied to URLs too (git+https identity destroyed)",
        VC, "replace",
        ("    return match.group(1) if match else requirement\n",
         '    return requirement.split("/")[0]\n'),
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_from_url_value_keeps_the_whole_url_as_identity",
    ),
    (
        "docker `-it` combined short boolean removed",
        VC, "append",
        '_DOCKER_BOOLEAN_FLAGS = _DOCKER_BOOLEAN_FLAGS - {"-it"}\n',
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_docker_combined_short_booleans_still_find_image",
    ),
    (
        "uvx --from downgraded known-positive -> value flag",
        VC, "append",
        "_UVX_POSITIVE_FLAGS = frozenset()\n"
        '_UVX_VALUE_FLAGS = _UVX_VALUE_FLAGS | {"--from"}\n',
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_known_positive_forms_unchanged",
    ),
    (
        # The README form is pinned by `--python`'s classification, which is
        # the half that was actually broken. `--from`'s classification is NOT
        # observable through this form -- see the test's docstring.
        "uvx --python reclassified -> README pin form misidentified as 3.12",
        VC, "append",
        '_UVX_VALUE_FLAGS = _UVX_VALUE_FLAGS - {"--python"}\n'
        '_UVX_BOOLEAN_FLAGS = _UVX_BOOLEAN_FLAGS | {"--python"}\n',
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_readme_documented_pin_form_resolves_to_the_package",
    ),
    (
        "uvx --quiet reclassified boolean -> value (rejected design)",
        VC, "append",
        '_UVX_BOOLEAN_FLAGS = _UVX_BOOLEAN_FLAGS - {"--quiet"}\n'
        '_UVX_VALUE_FLAGS = _UVX_VALUE_FLAGS | {"--quiet"}\n',
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_uvx_boolean_flag_still_finds_package",
    ),
    (
        "uvx positional normalized too (inline pin lost)",
        VC, "replace",
        ('            return ("pypi", _pep508_base_name(raw) '
         "if from_positive_flag else raw)\n",
         '            return ("pypi", _pep508_base_name(raw))\n'),
        "tests/test_version_checker.py::TestKnownPositiveValueFlags"
        "::test_positional_uvx_token_is_left_raw",
    ),
    (
        "uvx pin detection un-shared (back to independent scan)",
        HD, "replace",
        ("        raw, _from_flag = _uvx_package_arg(args)\n"
         '        if raw is not None and "==" in raw:\n'
         '            _, _, version = raw.partition("==")\n'
         "            return version or None\n",
         "        for arg in args:\n"
         '            if arg.startswith("-"):\n'
         "                continue\n"
         '            if "==" in arg:\n'
         '                _, _, version = arg.partition("==")\n'
         "                return version or None\n"),
        "tests/test_tools.py::TestUpdateServerVersionRepair"
        "::test_detect_effective_version_pin_matrix",
    ),
]


def purge() -> None:
    for d in MUT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


PY = str(REPO / ".venv/bin/python")
ENV = {**__import__("os").environ,
       "PYTHONDONTWRITEBYTECODE": "1",
       "PYTHONPATH": str(MUT / "src")}


def check_provenance() -> None:
    """Fail loudly if the tests would import the WORKTREE instead of the copy.

    Without this the whole run is theatre: every mutant would "survive" while
    the unmutated worktree source quietly served every import.
    """
    proc = subprocess.run(
        [PY, "-c", "import pmcp.manifest.version_checker as m; print(m.__file__)"],
        cwd=MUT, capture_output=True, text=True, env=ENV,
    )
    resolved = proc.stdout.strip()
    expected = str(MUT / "src/pmcp/manifest/version_checker.py")
    if resolved != expected:
        raise SystemExit(
            f"ABORT: tests would import\n  {resolved}\nnot the mutation copy\n  "
            f"{expected}\nEvery mutant would falsely survive."
        )
    print(f"import provenance OK -> {resolved}\n")


def run(node: str) -> tuple[bool, str]:
    purge()
    proc = subprocess.run(
        [PY, "-m", "pytest", node, "-q", "-p", "no:randomly", "--no-header", "-x"],
        cwd=MUT, capture_output=True, text=True, env=ENV,
    )
    tail = [ln for ln in proc.stdout.splitlines()
            if ln.startswith(("FAILED", "ERROR", "assert", "E "))
            or " passed" in ln or " failed" in ln or " error" in ln]
    return proc.returncode == 0, " | ".join(tail[-3:])[:260]


def main() -> int:
    originals = {VC: VC.read_text(), HD: HD.read_text()}
    purge()
    check_provenance()

    print("=" * 78)
    print("BASELINE (unmutated copy): every target node must PASS")
    print("=" * 78)
    baseline_bad = []
    for label, _f, _k, _p, node in MUTATIONS:
        ok, detail = run(node)
        print(f"  {'PASS' if ok else 'FAIL'}  {node.rsplit('::', 1)[1]}")
        if not ok:
            baseline_bad.append((node, detail))
    if baseline_bad:
        print("\nBASELINE BROKEN -- mutation results would be meaningless:")
        for n, d in baseline_bad:
            print(f"   {n}\n     {d}")
        return 1

    print()
    print("=" * 78)
    print("MUTATIONS: each must turn its ONE target node RED")
    print("=" * 78)
    survivors = []
    for label, path, kind, payload, node in MUTATIONS:
        for p, text in originals.items():
            p.write_text(text)
        if kind == "append":
            path.write_text(originals[path] + "\n" + payload)
        else:
            old, new = payload
            text = originals[path]
            if old not in text:
                print(f"  SKIP  {label}\n        (anchor not found -- mutation "
                      f"did not apply, treat as UNPROVEN)")
                survivors.append((label, node, "anchor not found"))
                continue
            path.write_text(text.replace(old, new, 1))

        ok, detail = run(node)
        verdict = "SURVIVED" if ok else "killed  "
        print(f"  {verdict}  {label}")
        print(f"            node: {node.rsplit('::', 1)[1]}")
        print(f"            {detail}")
        if ok:
            survivors.append((label, node, detail))

    for p, text in originals.items():
        p.write_text(text)
    purge()

    print()
    print("=" * 78)
    if survivors:
        print(f"{len(survivors)} SURVIVING MUTANT(S) -- these tests do not pin "
              "what they claim:")
        for label, node, detail in survivors:
            print(f"  - {label}\n      {node}\n      {detail}")
        return 1
    print(f"All {len(MUTATIONS)} mutants killed, each by its own single node.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
