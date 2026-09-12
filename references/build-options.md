# Build options and SDK snapshots

## Backend selection

- Use `--backend docker` for tags available in the third-party Docker mirror.
- Use `--backend local` for an installed SDK. `--local-sdk` without a path uses
  `/srv/mer/sdks/sfossdk/sdk-chroot`.
- `--backend auto` uses local mode when `--local-sdk` or `--target` is present;
  otherwise it uses Docker. An explicitly requested local backend never falls
  back silently.
- Use `--target <exact-name>` to select a registered, non-snapshot base target.
  The helper creates or reuses a managed snapshot and never builds directly in
  that base.
- For local SDK builds, let the helper use its privileged Docker wrapper. Do
  not launch `sdk-chroot` directly from Codex. The wrapper's passwd/group
  changes remain ephemeral and the real SDK root is not rewritten.

## Common commands

Preflight without pulling, cleaning, changing permissions, or building:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py \
  --project-dir . --backend local --release 5.2.0 \
  --arch aarch64 --dry-run --json
```

Build an image tag confirmed to exist in the third-party Docker mirror using
the recorded architecture:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py \
  --project-dir . --release <confirmed-image-tag> --backend docker --quiet
```

Build an exact installed target:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py \
  --project-dir . --backend local --target aarch64 --no-vcs-apply --quiet
```

Explicitly isolate and reuse one snapshot for browser work that needs the same
custom inputs:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py \
  --project-dir . --backend local --target aarch64 \
  --snapshot-key browser-esr153 --quiet
```

Build all image architectures with debug packages:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py \
  --project-dir . --backend local --release 5.2.0 --all --debug --quiet
```

Inject local dependency RPMs:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py \
  --project-dir . --backend local --target aarch64 \
  --local-rpms-dir /path/to/RPMS --allow-untrusted-rpms --quiet
```

Only pass `--allow-untrusted-rpms` when unsigned local packages are expected.
The local backend stages selected RPMs under project `.mb2` so paths outside
the SDK's mounted roots remain visible inside the chroot.

## Local SDK snapshots

- Keep one registered, release-managed base target per architecture. The base
  may contain the registration-provided repositories plus required
  `hw-common` and AppSupport repositories; do not install project repositories,
  packages, or local RPMs there.
- Ordinary builds that use only registered repositories and declared
  `BuildRequires` pass the registered `<base>` target to `mb2`; `mb2`
  automatically creates and shares `<base>.default`. Use an explicit project key when task or repository instructions require
  per-project isolation.
- Builds with `--snapshot-repository`, `--snapshot-package`, or
  `--local-rpms-dir` are isolated from the shared snapshot. The helper derives
  a stable key from the project, with `esrNNN` work mapping to
  `browser-esrNNN`; use `--snapshot-key` to deliberately share one custom
  input environment across related projects.
- Isolated environments use an original target named `<base>-<key>`, reset
  from `<base>` with `sdk-manage target snapshot --reset=outdated`. Custom
  inputs are applied to that original, then `mb2` creates and manages its
  `<base>-<key>.default` working snapshot. The helper must pass the original
  target to `mb2`, never its `.default` child; otherwise `mb2` creates a broken
  `.default.default` chain.
- A reset intentionally removes snapshot-only repositories and packages. Pass
  persistent inputs on every applicable build with repeatable
  `--snapshot-repository [ALIAS=]URL`, `--snapshot-package`, and
  `--local-rpms-dir` options. The helper reapplies them before `mb2 build
  --prepare`, which restores declared BuildRequires.
- Retain registered architecture bases and their shared, `mb2`-managed
  `.default` snapshots. Remove obsolete isolated originals with `sdk-manage`,
  including their children, after confirming that no build is active. Do not
  remove target directories directly.

Use `--pull-policy always|missing|never` to control Docker image pulls.
`always` preserves the release-build default. Use `missing` for reproducible
offline-friendly iteration and `never` when the image must already exist.

## Behavior and safeguards

- The helper takes a non-blocking project lock. Never launch overlapping
  builds in the same project; a second invocation exits with the lock owner.
- The standard `.default` snapshot is shared by ordinary projects, and a named
  snapshot may be shared by projects with the same custom inputs. Rely on
  `mb2` to coordinate concurrent snapshot modifications; do not add a separate
  helper-level per-snapshot lock.
- Docker builds use an internal shadow copy and sync generated outputs back.
- Local SDK builds run as the project owner and default to `--no-vcs-apply`.
  Pass `--vcs-apply` only when that behavior is deliberately wanted.
- All local build preparation and dependency installation targets the managed
  project snapshot. Treat mutation of the registered base as an SDK
  administration error.
- Architecture switches remove known untracked qmake/CMake outputs. `--clean`
  requests the same generated-artifact cleanup explicitly. Tracked source is
  excluded.
- Docker source access uses read-only ACLs plus write ACLs limited to the
  project root, `.mb2`, translations, and known generated outputs. Use
  `--permission-fallback chmod` only when broad permission changes are
  acceptable.
- `--debug` passes `-d` to `mb2`; this strips main binaries and emits debug
  packages. Interpret requests for “stripped RPMs” as this mode.
- Metadata is written atomically to
  `.mb2/build-sailfishos-skill-last-build.json`. It includes helper/schema
  versions, backend, image ID or local SDK, targets, durations, artifacts,
  rpmlint counts, and a classified failure when applicable.
- The full last build log is
  `.mb2/build-sailfishos-skill-last.log`. Missing `BuildRequires` failures also
  trigger target-side provider diagnostics.
