#!/usr/bin/env node
'use strict'

// Name an npm/npx server's package using **npm's own parser**, or refuse.
//
// Consiliency/pmcp#195. The hand-written flag tables in `version_checker.py`
// have been repaired five times (#180 -> #192 -> #194 -> #195 -> the 2.5.2
// nullable-spelling fix) and every defect was in the rules *around* the tables,
// not in a missing entry. Each repair produced a *confident wrong answer*, and
// `refresher._same_package` reads a matching identity as POSITIVE CONFIRMATION
// that a cached tool description still describes the configured package -- so a
// wrong answer serves one server another server's tool descriptions.
//
// This helper answers exactly one question -- "can we name this package with
// certainty?" -- using the host npm's own `nopt`, its own `@npmcli/config`
// definitions, its own `npm-package-arg`, and a faithful port of the
// `npx-cli.js` pre-scan. Anything it cannot answer with certainty is REFUSED.
// A refusal costs auto-update coverage for that server; a wrong answer is a
// correctness bug. Refusal is always the cheaper failure.
//
// Wire protocol: NDJSON on stdin/stdout, one JSON object per line.
//
//   parent <- {"handshake":1,"status":"OK","npmVersion":..,"npmRoot":..,
//              "npxCliSha256":..}
//   parent <- {"handshake":1,"status":"UNAVAILABLE","reason":..}
//   parent <- {"handshake":1,"status":"REFUSED","reason":..}
//   parent -> {"id":N,"command":"npx","args":[..]}
//   parent <- {"id":N,"status":"IDENTITY","spec":".."}
//   parent <- {"id":N,"status":"REFUSED","reason":".."}
//   parent <- {"id":N,"status":"STALE","npmVersion":".."}
//
// The handshake is emitted BEFORE any query is accepted and the parent must
// consume it before its first write: the self-test runs at spawn over this same
// stdout, so without a handshake the first `resolve()` could read a self-test
// line as its own answer -- a wrong `Identity`, the same mis-attribution class
// as an unmatched in-flight response.
//
// Argv always arrives as parsed JSON. It is never a shell string and never
// interpolated into `-e`.

const fs = require('fs')
const path = require('path')
const crypto = require('crypto')
const { createRequire } = require('module')
const { execFileSync } = require('child_process')
const readline = require('readline')

// ---------------------------------------------------------------------------
// Drift tripwire
// ---------------------------------------------------------------------------
//
// The self-test below is TAUTOLOGICAL with respect to the ported pre-scan --
// the port and its expected values were frozen by the same author, so a port
// that misreads `npx-cli.js` produces a self-test that agrees with it. The
// hash is the only thing that can detect pre-scan drift, so it has a specified
// firing action: a mismatch REFUSES for the process lifetime, exactly like a
// failed self-test.
//
// Verified by downloading the published tarballs: `bin/npx-cli.js` is
// BYTE-IDENTICAL across npm 10.8.2, 10.9.4, 11.0.0, 11.6.2 and 11.19.0. npm
// 9.9.4 differs (cf7b8e46...) and is deliberately NOT accepted -- npm 9 is EOL
// and its pre-scan is a different program, so refusing is the honest answer.
const KNOWN_NPX_CLI_SHA256 = new Set([
  // npm 10.8.2 .. 11.19.0
  '237adf8f3747cad8b9b62fcfd0d9c8d509a64e550337707f55100afcb79e8900',
])

// ---------------------------------------------------------------------------
// Step 1 (child half): the parsed-config-key allowlist
// ---------------------------------------------------------------------------
//
// **This is an allowlist of plain things, not a denylist of unusual ones.**
// Three board rounds on a denylist each found another way for a *confident*
// answer to be wrong, because every `npm_config_*` option is equally settable
// as a command-line flag and npm keeps inventing new ones. Measured cost of the
// allowlist: zero. All 79 npm-family servers in `manifest.yaml` parse to
// `{yes}` alone or to `{}`.
//
// `yes` cannot redirect resolution (it only suppresses the install prompt) and
// `package` IS the resolution input, read explicitly below. Every other key --
// `registry`, `userconfig`, `prefix`, `cache`, `call`, `workspace`, and any key
// npm has not invented yet, including unknown flags, which nopt reports as
// boolean keys -- may change WHICH package npm fetches or WHERE from, so it
// refuses.
const ALLOWED_CONFIG_KEYS = new Set(['yes', 'package'])

// `npm dlx` is pnpm/yarn spelling; `npm dlx probe` is `Unknown command`. It sits
// in `version_checker._NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND`, so the tables
// mint an identity for a server that can never launch. Excluded here.
const NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND = new Set([
  'exec',
  'x',
  'install',
  'i',
  'add',
])

// npm honours the `package` config for `exec`/`x` ONLY, and those are also the
// only subcommands whose trailing positionals are arguments to a binary rather
// than more packages. The install family differs on both counts, and treating
// it like `exec` produced three separate wrong answers (board review on the
// diff, correctness seat):
//
//   npm install pkg-real --package=pkg-other -> 'pkg-other'  (npm fetches pkg-real)
//   npm install --package                    -> 'true'       (npm fetches nothing)
//   npm install a b                          -> 'a'  ┐ two different configs
//   npm install a c                          -> 'a'  ┘ collapsed onto ONE identity
//
// The last is the #180 collision class exactly: `_same_package` would confirm
// one server's cached tool descriptions against the other's config.
const NPM_EXEC_SUBCOMMANDS = new Set(['exec', 'x'])

// npa types whose `name` is the package npm actually fetches. `alias` is
// excluded deliberately and is the reason this is an allowlist rather than a
// "has a name" check: `npx -y myalias@npm:left-pad` RUNS `left-pad`, but npa
// reports `{type:'alias', name:'myalias'}` -- a perfectly valid name that is
// not the package. Minting it would make the version check query a squattable
// name, and swapping the alias target (`a@npm:x` -> `a@npm:y`) would leave the
// identity unchanged so the freshness gate confirms TRUE and serves x's
// descriptions for y. `git`, `remote`, `file` and `directory` refuse here too.
const ACCEPTED_NPA_TYPES = new Set(['tag', 'version', 'range'])

// ---------------------------------------------------------------------------
// npm discovery
// ---------------------------------------------------------------------------

function whichRealpath (name) {
  const dirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean)
  for (const dir of dirs) {
    const candidate = path.join(dir, name)
    try {
      if (fs.statSync(candidate).isFile() || fs.lstatSync(candidate).isSymbolicLink()) {
        return fs.realpathSync(candidate)
      }
    } catch {
      // not here; keep looking
    }
  }
  return null
}

function isNpmRoot (dir) {
  try {
    if (!fs.existsSync(path.join(dir, 'bin', 'npx-cli.js'))) {
      return false
    }
    const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'))
    return manifest && manifest.name === 'npm'
  } catch {
    return false
  }
}

// Walk up from a realpath'd npm/npx entry point to the npm package root, i.e.
// the directory that holds `package.json`, `bin/npx-cli.js` and `node_modules`.
//
// Two layouts, both real and both checked at every level:
//
//   1. `<root>/bin/npx-cli.js` -- the usual case, where `PATH`'s `npx` is a
//      symlink straight at it, so walking up one level lands on `<root>`.
//   2. `<prefix>/bin/npx` beside `<prefix>/lib/node_modules/npm` -- npm's own
//      global-install layout. Some distributions ship `<prefix>/bin/npx` as a
//      *copy* of `npx-cli.js` rather than a symlink, and then no amount of
//      walking up from it ever reaches the npm package. Verified on the GitHub
//      Actions runner's bundled node20 tree (npm 10.8.2), where the previous
//      code found nothing and the resolver fell back to the flag tables.
//
// The prefix form is checked **only against the entry point's own prefix**, not
// at every level of the walk. Checking it at every level attributes an
// unrelated npm to the entry point: on the development host for this change,
// `/lib/node_modules/npm` exists (a system npm 11.6.2), so a stray `npx` in a
// temp directory walked up to `/` and adopted it -- naming a parser that has
// nothing to do with the binary that would actually run.
function rootFromEntryPoint (entry) {
  let dir = path.dirname(entry)
  const viaPrefix = path.join(path.dirname(dir), 'lib', 'node_modules', 'npm')
  if (isNpmRoot(viaPrefix)) {
    return viaPrefix
  }
  for (let i = 0; i < 6; i++) {
    if (isNpmRoot(dir)) {
      return dir
    }
    const parent = path.dirname(dir)
    if (parent === dir) {
      break
    }
    dir = parent
  }
  return null
}

function resolveNpmRoot () {
  const npxEntry = whichRealpath('npx')
  const npmEntry = whichRealpath('npm')
  // Whether npm is INSTALLED is a different question from whether we can find
  // its parser. `UNAVAILABLE` -- the one state permitted to fall back to the
  // flag tables -- means "npm is absent", so an npm we can see but cannot
  // locate must REFUSE instead.
  const sawEntryPoint = Boolean(npxEntry || npmEntry)
  const npxRoot = npxEntry ? rootFromEntryPoint(npxEntry) : null
  const npmRoot = npmEntry ? rootFromEntryPoint(npmEntry) : null

  // Two different npm installations on PATH would mean `npx <pkg>` and
  // `npm exec <pkg>` are parsed by different programs, and this helper answers
  // for both. Refuse rather than pick one.
  if (npxRoot && npmRoot && npxRoot !== npmRoot) {
    return { root: null, split: [npxRoot, npmRoot], sawEntryPoint }
  }
  const found = npxRoot || npmRoot
  if (found) {
    return { root: found, split: null, sawEntryPoint }
  }

  // Fall back to `npm root -g`, which names `<prefix>/lib/node_modules`.
  try {
    const out = execFileSync('npm', ['root', '-g'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 5000,
    }).trim()
    const candidate = path.join(out, 'npm')
    if (isNpmRoot(candidate)) {
      return { root: fs.realpathSync(candidate), split: null, sawEntryPoint }
    }
  } catch {
    // no npm at all
  }
  return { root: null, split: null, sawEntryPoint }
}

// ---------------------------------------------------------------------------
// Parser loading
// ---------------------------------------------------------------------------

// `nopt`, the config definitions and `npm-package-arg` are required through a
// `createRequire` rooted at the resolved `npx-cli.js`, so the modules always
// come from the same npm installation the pre-scan was read from. Requiring
// them from this file's own location would silently mix one npm's definitions
// with another npm's pre-scan.
function loadParser (npmRoot) {
  const npxCli = path.join(npmRoot, 'bin', 'npx-cli.js')
  const req = createRequire(npxCli)
  const nopt = req('nopt')
  const { definitions, shorthands } = req('@npmcli/config/lib/definitions')
  const npa = req('npm-package-arg')

  // `types[k] = definitions[k].type` is npm's own `getTypesFromDefinitions`
  // (`@npmcli/config/lib/index.js:1017`). npm's invalidHandler /
  // unknownHandler / abbrevHandler are all warning-only and cannot change
  // `remain`, so omitting them cannot change the answer.
  const types = {}
  for (const [key, def] of Object.entries(definitions)) {
    types[key] = def.type
  }
  return { nopt, definitions, shorthands, npa, types, npxCli }
}

// ---------------------------------------------------------------------------
// The `npx-cli.js` pre-scan, ported
// ---------------------------------------------------------------------------
//
// A faithful port of `npx-cli.js` lines 10-123, including all three of its
// sets, applied on the same index basis. `npx-cli.js:7` does
// `process.argv.splice(2, 0, 'exec')` and the loop starts at `i = 3` *on that
// basis*, so the array here is `[node, npm-cli.js, 'exec', ...args]` and the
// leading `'exec'` survives into `remain` -- which is why the identity is
// `remain[1]`, not `remain[0]`. `exec` is a real published package (the
// registry returns HTTP 200 for it), so reading `remain[0]` would have made
// gateway.update_server probe `npx -y exec@latest --help` for every npx server
// in the manifest.
//
// `switches` and `opts` are recomputed from the HOST's own definitions rather
// than hardcoded, so a definition npm adds later is classified the way the host
// npm classifies it. npx-cli's own hardcoded extras (`removedSwitches`,
// `no-install`, `quiet`, `q`, `version`, `v`, `help`, `h`, and the whole `opts`
// set) are included verbatim: dropping them would let a later `--package=`
// outrank the positional, because no `--` gets inserted.
function npxPreScan (args, definitions, shorthands) {
  const argv = ['node', 'npm-cli.js', 'exec', ...args]

  const removedSwitches = new Set([
    'always-spawn',
    'ignore-existing',
    'shell-auto-fallback',
  ])
  const removedOpts = new Set(['npm', 'node-arg', 'n'])
  const removed = new Set([...removedSwitches, ...removedOpts])

  const npmSwitches = Object.entries(definitions)
    .filter(([, { type }]) => type === Boolean ||
      (Array.isArray(type) && type.includes(Boolean)))
    .map(([key]) => key)

  const switches = new Set([
    ...removedSwitches,
    ...npmSwitches,
    'no-install',
    'quiet',
    'q',
    'version',
    'v',
    'help',
    'h',
  ])

  const opts = new Set([
    ...removedOpts,
    'package',
    'p',
    'cache',
    'userconfig',
    'call',
    'c',
    'shell',
    'npm',
    'node-arg',
    'n',
  ])

  let i
  for (i = 3; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--') {
      break
    } else if (/^-/.test(arg)) {
      const [key, ...v] = arg.replace(/^-+/, '').split('=')

      switch (key) {
        case 'p':
          argv[i] = ['--package', ...v].join('=')
          break

        case 'shell':
          argv[i] = ['--script-shell', ...v].join('=')
          break

        case 'no-install':
          argv[i] = '--yes=false'
          break

        default:
          if (shorthands[key] && !removed.has(key)) {
            const a = [...shorthands[key]]
            if (v.length) {
              a.push(v.join('='))
            }
            argv.splice(i, 1, ...a)
            i--
            continue
          }
          break
      }

      if (removed.has(key)) {
        // npx-cli.js prints a deprecation notice here. This helper stays silent:
        // stdout is the NDJSON channel and a stray line would be read as a
        // response.
        argv.splice(i, 1)
        i--
      }

      if (v.length === 0 && !switches.has(key) &&
          (opts.has(key) || !/^-/.test(argv[i + 1]))) {
        if (removed.has(key)) {
          argv.splice(i + 1, 1)
        } else {
          i++
        }
      }
    } else {
      argv.splice(i, 0, '--')
      break
    }
  }

  return argv
}

// ---------------------------------------------------------------------------
// The resolution contract
// ---------------------------------------------------------------------------

const REFUSE = (reason) => ({ status: 'REFUSED', reason })

function resolveOne (parser, command, args) {
  const { nopt, definitions, shorthands, npa, types } = parser

  if (command !== 'npx' && command !== 'npm') {
    return REFUSE(`command is not a bare npx/npm: ${command}`)
  }
  if (!Array.isArray(args) || args.some((a) => typeof a !== 'string')) {
    return REFUSE('args is not a list of strings')
  }

  // Step 2 -- parse, exactly as npm would.
  let parsed
  let remain
  try {
    const argv = command === 'npx'
      ? npxPreScan(args, definitions, shorthands)
      : ['node', 'npm-cli.js', ...args]
    parsed = nopt(types, shorthands, argv, 2)
    remain = parsed.argv.remain
    delete parsed.argv
  } catch (err) {
    // `--__proto__=evil` and `--constructor` throw inside nopt (Object.prototype
    // leaking into the shorthand lookup), and REAL npx dies on the former inside
    // the pre-scan itself. A contained failure is a refusal for THIS request
    // only; the process stays healthy and answers the next query correctly.
    return REFUSE(`parser threw: ${err && err.message}`)
  }

  // Step 1 (child half) -- the parsed-config-key allowlist.
  for (const key of Object.keys(parsed)) {
    if (!ALLOWED_CONFIG_KEYS.has(key)) {
      return REFUSE(`config key outside the allowlist: ${key}`)
    }
  }

  // Step 2a -- WHICH SUBCOMMAND. This runs FIRST and UNCONDITIONALLY, before
  // `--package` is even looked at.
  //
  // **It used to run only in the `--package`-absent branch, and that reopened
  // Consiliency/pmcp#183 in full** (board review on the diff, correctness
  // seat). Measured on the broken code:
  //
  //     npm run   --package=pkg-a -- bin  ->  ('npm', 'pkg-a')   WRONG
  //     npm start --package=pkg-a         ->  ('npm', 'pkg-a')   WRONG
  //     npm test  --package=pkg-a         ->  ('npm', 'pkg-a')   WRONG
  //     npm dlx   --package=pkg-a -- bin  ->  ('npm', 'pkg-a')   WRONG
  //     npm rum   --package=pkg-a -- bin  ->  ('npm', 'pkg-a')   WRONG
  //     npm       --package=pkg-a         ->  ('npm', 'pkg-a')   WRONG
  //
  // `npm run` does not run a registry package; `handlers.py` would then have
  // fetched and executed `pkg-a@latest --help` off the public registry, which
  // is the #183 hazard exactly. `--package` says which package a command comes
  // FROM; it cannot turn a script runner into a package installer. The two
  // questions -- "does this subcommand name a package at all" and "where does
  // the name come from" -- are independent, and the first gates the second.
  if (command === 'npm') {
    if (remain.length < 1 || !NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND.has(remain[0])) {
      // `npm run mcp`, `npm start`, `npm test`, `npm create foo`, a typo like
      // `npm rum mcp`, bare `npm -y pkg`, and `npm dlx x` all land here.
      return REFUSE(`npm subcommand has no package operand: ${remain[0]}`)
    }
  } else if (remain[0] !== 'exec') {
    // Structural tripwire: `npxPreScan` always builds `[node, npm-cli.js,
    // 'exec', ...]` and nopt slices at 2, so `remain[0]` is `'exec'` unless the
    // port has drifted.
    return REFUSE('npx pre-scan did not yield the expected leading "exec"')
  }

  // Step 2b -- WHERE THE NAME COMES FROM.
  //
  // `npm install` takes a LIST of packages and does not read the `package`
  // config at all, so exactly one positional and no `--package` is the only
  // shape that names one package. Anything else refuses.
  if (command === 'npm' && !NPM_EXEC_SUBCOMMANDS.has(remain[0])) {
    if (Object.prototype.hasOwnProperty.call(parsed, 'package')) {
      return REFUSE(`npm ${remain[0]} does not read --package`)
    }
    if (remain.length !== 2) {
      return REFUSE(
        `npm ${remain[0]} names ${remain.length - 1} packages, not one`
      )
    }
  }

  let candidate = null
  // `package` is typed `[String, Array]`, so nopt always yields a LIST. Test it
  // by KEY PRESENCE, never by truthiness: `npx --package=""` parses to
  // `{package: [""]}`, and `parsed.package || remain[0]` would mint the
  // positional while npm actually fetches `/undefined`.
  if (Object.prototype.hasOwnProperty.call(parsed, 'package')) {
    const pkgs = parsed.package
    if (!Array.isArray(pkgs) || pkgs.length === 0 || new Set(pkgs).size !== 1) {
      // npm allows `--package` to be repeated. ONE DISTINCT package is an
      // identity; several are not, and picking the first would be exactly the
      // guess this exists to stop. Repeating the SAME package is still one
      // identity, so it is compared as a set rather than by length.
      return REFUSE('--package must name exactly one distinct package')
    }
    // A single `--package` value outranks the positional (Consiliency/pmcp#182):
    // in `npm exec --package=<pkg> -- <bin>` the positional is the BINARY npm
    // runs FROM that package, not a package.
    candidate = pkgs[0]
  } else {
    if (remain.length < 2) {
      return REFUSE('no package operand')
    }
    candidate = remain[1]
  }

  if (typeof candidate !== 'string' || candidate === '') {
    return REFUSE('empty package spec')
  }

  // A leading `-` is refused BEFORE npa sees it, because the npm majors
  // disagree about what such a token names and npa is where they disagree.
  // Measured against both real binaries with a dead registry:
  //
  //   npm 10.9.4: `npm exec -- --flag-thing` fetches `/--flag-thing`
  //               (npa 12 returns `{type:'range', name:'--flag-thing'}`)
  //   npm 11.19.0: the same argv fetches `/undefined`
  //               (npa 13 returns `{type:'tag', name:undefined}`)
  //
  // Two installed npms, two different packages, same command line. That is
  // precisely an identity we cannot state with certainty, so it refuses --
  // and refusing keeps the answer the SAME on both, which is what lets the
  // spawn-time self-test be an invariant rather than a description of
  // whichever npm the author happened to have.
  if (candidate.startsWith('-')) {
    return REFUSE('package operand starts with "-"; npm majors disagree on it')
  }

  // Step 3 -- validate through the host's own npm-package-arg.
  let spec
  try {
    spec = npa(candidate)
  } catch (err) {
    return REFUSE(`npa rejected the spec: ${err && err.message}`)
  }
  if (!ACCEPTED_NPA_TYPES.has(spec.type)) {
    return REFUSE(`npa type is not a registry spec: ${spec.type}`)
  }
  if (!spec.name) {
    return REFUSE('npa produced no package name')
  }

  // Returned RAW, tag intact. `_strip_npm_tag` stays in `detect_package_type`
  // because gateway.update_server's pin detection needs the suffix to tell a
  // pinned server from an unpinned one.
  return { status: 'IDENTITY', spec: candidate, name: spec.name, npaType: spec.type }
}

// ---------------------------------------------------------------------------
// Self-test -- the invariant corpus
// ---------------------------------------------------------------------------
//
// Run ONCE at spawn against the host's own nopt and definitions. Any failure
// refuses every query for the process lifetime.
//
// **A failed self-test must never fall back to the flag tables.** A self-test
// failure is precisely the evidence that the host's parser behaves in a way
// this code does not model; answering by consulting the known-incomplete tables
// is the worst available choice. Falling back is fail-OPEN; refusing is
// fail-safe.
//
// Every expectation below is a FROZEN LITERAL, never recomputed from the code
// under test.
const SELF_TEST = [
  ['npx', ['-y', 'left-pad'], 'left-pad'],
  ['npx', ['left-pad'], 'left-pad'],
  ['npx', ['-y', '@scope/pkg'], '@scope/pkg'],
  ['npx', ['-y', 'pkg@1.2.3'], 'pkg@1.2.3'],
  ['npx', ['--package=p', '--', 'bin'], 'p'],
  ['npx', ['--yes=maybe', 'tok'], 'maybe'],
  ['npm', ['exec', 'pkg'], 'pkg'],
  ['npm', ['x', 'pkg'], 'pkg'],
  ['npm', ['exec', '--package=pkg', '--', 'bin'], 'pkg'],
  ['npx', ['-y'], null],
  ['npx', ['--'], null],
  ['npx', ['--package='], null],
  ['npx', ['--package=a', '--package=b', 'bin'], null],
  ['npx', ['--package=a', '--package=a', 'bin'], 'a'],
  ['npx', ['--pack', 'zz', 'bin'], null],
  ['npx', ['--userconfig', '/tmp/rc', 'probe'], null],
  ['npx', ['--registry', 'http://x', 'probe'], null],
  ['npx', ['-y', 'myalias-zz@npm:left-pad'], null],
  ['npx', ['-y', 'github:owner/repo'], null],
  ['npm', ['run', 'mcp'], null],
  ['npm', ['start'], null],
  ['npm', ['test'], null],
  ['npm', ['create', 'foo'], null],
  ['npm', ['dlx', 'x'], null],
  // The install family: a package LIST, and no `--package` config.
  ['npm', ['install', 'a'], 'a'],
  ['npm', ['install', 'a', 'b'], null],
  ['npm', ['install', 'a', '--package=b'], null],
  ['npm', ['install', '--package'], null],
  ['npm', ['install'], null],
  ['npm', ['i', 'a', 'b'], null],
  ['npm', ['add', 'a', 'b'], null],
  // ...while exec's trailing positionals are ARGUMENTS to the binary, so these
  // must keep resolving.
  ['npm', ['exec', 'pkg-one', 'pkg-two'], 'pkg-one'],
  ['npx', ['-y', 'pkg-one', 'pkg-two'], 'pkg-one'],
  // `--package` must not short-circuit the subcommand allowlist (#183).
  ['npm', ['run', '--package=pkg-a', '--', 'bin'], null],
  ['npm', ['start', '--package=pkg-a'], null],
  ['npm', ['test', '--package=pkg-a'], null],
  ['npm', ['dlx', '--package=pkg-a', '--', 'bin'], null],
  ['npm', ['rum', '--package=pkg-a', '--', 'bin'], null],
  ['npm', ['--package=pkg-a'], null],
  // ...while npx has no subcommand, so this one must still resolve.
  ['npx', ['--package=pkg-a', '--', 'bin'], 'pkg-a'],
  ['npm', ['-y', 'server-pkg'], null],
  ['npm', ['exec', '--', '--flag-thing'], null],
]

function selfTest (parser) {
  const failures = []
  for (const [command, args, expected] of SELF_TEST) {
    let got
    try {
      const result = resolveOne(parser, command, args)
      got = result.status === 'IDENTITY' ? result.spec : null
    } catch (err) {
      got = `<threw: ${err && err.message}>`
    }
    if (got !== expected) {
      failures.push(`${command} ${JSON.stringify(args)}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(got)}`)
    }
  }
  return failures
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function emit (obj) {
  process.stdout.write(JSON.stringify(obj) + '\n')
}

function readNpmVersion (npmRoot) {
  try {
    return JSON.parse(fs.readFileSync(path.join(npmRoot, 'package.json'), 'utf8')).version
  } catch {
    return null
  }
}

function main () {
  // A parent that dies mid-answer leaves this process writing to a closed
  // pipe. Without a handler node throws an unhandled EPIPE and prints a stack
  // trace; exiting quietly is the same outcome without the noise.
  process.stdout.on('error', () => process.exit(0))

  const { root: npmRoot, split, sawEntryPoint } = resolveNpmRoot()

  if (split) {
    // Two npm installations on PATH: `npx` and `npm` would be parsed by
    // different programs. REFUSED, not UNAVAILABLE -- we learned that the host
    // is ambiguous, which is the opposite of learning nothing.
    emit({ handshake: 1, status: 'REFUSED', reason: `two npm roots on PATH: ${split.join(' vs ')}` })
    return
  }
  if (!npmRoot) {
    if (sawEntryPoint) {
      // npm IS on PATH; we simply could not locate the package its parser
      // lives in. That is not absence, so it must not reach the flag tables --
      // the same fail-open the missing-helper and unspawnable-node cases were
      // just corrected for (board review on the diff).
      emit({
        handshake: 1,
        status: 'REFUSED',
        reason: 'npm is on PATH but its package root could not be located',
      })
      return
    }
    // UNAVAILABLE is reserved for exactly this: we learned nothing about npm,
    // only that it is not here. The caller falls back to the flag tables, which
    // is the pre-#195 behaviour and the only thing a node-less host can do.
    emit({ handshake: 1, status: 'UNAVAILABLE', reason: 'npm is not installed' })
    return
  }

  const npxCli = path.join(npmRoot, 'bin', 'npx-cli.js')
  let sha = null
  try {
    sha = crypto.createHash('sha256').update(fs.readFileSync(npxCli)).digest('hex')
  } catch (err) {
    emit({ handshake: 1, status: 'REFUSED', reason: `cannot read npx-cli.js: ${err && err.message}` })
    return
  }
  if (!KNOWN_NPX_CLI_SHA256.has(sha)) {
    emit({ handshake: 1, status: 'REFUSED', reason: `npx-cli.js hash not recognised: ${sha}` })
    return
  }

  let parser
  try {
    parser = loadParser(npmRoot)
  } catch (err) {
    // npm IS here but its parser is not where npm's own code puts it. That is
    // not "we learned nothing" -- it is an npm this helper cannot model, and
    // answering from tables generated against a DIFFERENT npm would be the
    // fail-open this change exists to remove.
    emit({ handshake: 1, status: 'REFUSED', reason: `cannot load npm's parser: ${err && err.message}` })
    return
  }

  const failures = selfTest(parser)
  if (failures.length) {
    emit({ handshake: 1, status: 'REFUSED', reason: `self-test failed: ${failures.join('; ')}` })
    return
  }

  const npmVersion = readNpmVersion(npmRoot)
  emit({
    handshake: 1,
    status: 'OK',
    npmVersion,
    npmRoot,
    npxCliSha256: sha,
  })

  const rl = readline.createInterface({ input: process.stdin })
  rl.on('line', (line) => {
    if (!line.trim()) {
      return
    }
    let request
    try {
      request = JSON.parse(line)
    } catch (err) {
      emit({ id: null, status: 'REFUSED', reason: 'malformed request' })
      return
    }
    const id = request && typeof request.id === 'number' ? request.id : null

    // Re-stat npm's own package.json per resolve, so an in-place npm upgrade
    // cannot leave a require-cached parser answering with stale definitions.
    const current = readNpmVersion(npmRoot)
    if (current !== npmVersion) {
      emit({ id, status: 'STALE', npmVersion: current })
      rl.close()
      process.exit(0)
      return
    }

    // Per-request try/catch is mandatory and wraps the pre-scan as well as
    // nopt: a poisoned argv must refuse THIS request without flipping the
    // process to a sticky state.
    let result
    try {
      result = resolveOne(parser, request.command, request.args)
    } catch (err) {
      result = REFUSE(`uncaught: ${err && err.message}`)
    }
    emit({ id, ...result })
  })

  // Exit on stdin end, so a parent crash cannot orphan this child.
  rl.on('close', () => process.exit(0))
}

main()
