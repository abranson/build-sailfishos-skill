---
name: build-sailfishos
description: Preflight, build and diagnose SailfishOS RPM projects with Docker or an installed Platform SDK. Use for target selection, local dependencies and RPM artifacts.
---

# Build SailfishOS

Prefer Sailfish Devel MCP build tools when available; otherwise use
`scripts/build_sailfishos.py`. Keep backend logic in this helper rather than
reconstructing Docker, SDK-chroot or mb2 commands.

## Build workflow

1. Read the project's build instructions. Pass its root as `project_path`
   (MCP) or `--project-dir`; the helper can discover one matching child root.
2. When target, release, local RPMs or cleanup is uncertain, use
   `sailfish_build_preflight` or `--dry-run --json` before building.
3. Prefer installed SDK `live` metadata for local builds. An explicit local
   backend must not silently fall back. Docker mirror tags describe available
   images, not the current SailfishOS release. Prefer a confirmed explicit tag.
4. Keep registered SDK bases pristine. Ordinary helper builds share mb2's
   `.default`; custom inputs get an isolated original target. **When task or
   repository instructions require per-project isolation, supply a stable
   `snapshot_key` / `--snapshot-key` even without custom inputs.** Keep that key
   on subsequent builds. Never pass a `.default` target to mb2 directly.
5. Start `sailfish_build_rpm`, or invoke the helper with `--quiet`. Use the
   architecture recorded in `.mb2/target` unless another was requested.
6. Report RPM paths and warnings from the completion metadata. The default
   artifact location is `RPMS/<release>/<arch>/<release|debug>/`.

## Wait and inspect efficiently

Leave status `lines=0`. Use a single short bounded wait only when completion is
near; for long work, use the MCP's `sailfish-devel-jobs wait --job-id ID` CLI
from its installation or checkout (`bin/sailfish-devel-jobs`). Run it in one
quiet terminal process and wait mechanically; do not repeatedly ask the model
to poll. For occasional status calls, pass the previous `revision` as
`after_revision`; unchanged results omit repeated details.

Use `sailfish_obs_watch` for long OBS waits, then the same build-status or CLI
waiter. Direct helper builds already block until completion: run one command
with `--quiet`, preserving its exit status.

Read `.mb2/build-sailfishos-skill-last-build.json` for direct build results.
The complete log is `.mb2/build-sailfishos-skill-last.log`. MCP failures include
a bounded excerpt; request more with `log_offset`/`max_bytes` or CLI `log` only
when that excerpt is insufficient. Do not load entire build logs routinely.

## Conditional details

- For exact targets, snapshot recipes, unsigned local RPMs, debug packages,
  cleanup, permissions or Docker pull policy, read
  [build-options.md](references/build-options.md).
- For missing dependencies, SDK refresh, stale outputs or unavailable targets,
  read [troubleshooting.md](references/troubleshooting.md).

Local builds default to no VCS application; explicitly enabling it is deliberate.
Use `--debug` when stripped main binaries plus debug RPMs are wanted. Never
launch overlapping builds in one project; the helper enforces a project lock.
