---
name: build-sailfishos
description: Build and validate SailfishOS RPM projects with the third-party `coderus/sailfishos-platform-sdk` Docker mirror or an installed `/srv/mer` SDK target. Use for release/devel builds, target and architecture discovery, packaging validation, local-RPM dependency injection, debug packages, build preflight, stale-artifact cleanup, and archived RPM output.
---

# Build SailfishOS

Use `scripts/build_sailfishos.py` instead of reconstructing Docker, SDK-chroot,
or `mb2` commands. Replace `<skill-dir>` below with this skill's directory.

## Workflow

1. Pass the project root with `--project-dir`. It must contain `rpm/*.spec`, or
   contain exactly one matching directory one level below it.
2. Run `--dry-run --json` when backend, release, target, image availability,
   local RPMs, or cleanup behavior is uncertain.
3. For an installed SDK, prefer `live` and inspect the selected target's
   metadata; that is the authoritative current target on the machine. For a
   Docker build, prefer an explicit image tag. Otherwise the helper checks
   `SAILFISHOS_RELEASE`, workflow `RELEASE:` values, then the newest tag in the
   third-party `coderus/sailfishos-platform-sdk` mirror. Never treat that
   mirror's newest tag as the current SailfishOS release or SDK target.
4. Prefer the architecture in `.mb2/target` when the user did not name one.
5. Report RPMs from `RPMS/<release>/<arch>/<release|debug>/` or the explicit
   `--artifacts-dir`.

## Execution and output

- When the Sailfish Devel MCP is available, prefer its asynchronous build tool.
  Monitor with bounded `wait_seconds` calls and `lines=0`; request log lines
  only after failure or when the user needs live progress.
- For direct helper builds, pass `--quiet`. Successful builds then print only
  their RPM paths. Failed commands print a bounded tail while the complete
  build output remains in `.mb2/build-sailfishos-skill-last.log`.
- Use `.mb2/build-sailfishos-skill-last-build.json` for status, artifacts, and
  classified failures instead of feeding the full build log back to the model.

## Backend selection

- Use `--backend docker` for tags available in the third-party Docker mirror.
- Use `--backend local` for an installed SDK. `--local-sdk` without a path uses
  `/srv/mer/sdks/sfossdk/sdk-chroot`.
- `--backend auto` uses local mode when `--local-sdk` or `--target` is present;
  otherwise it uses Docker. An explicitly requested local backend never falls
  back silently.
- Use `--target <exact-name>` to select an installed target, including targets
  whose names are more specific than their architecture.
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

Use `--pull-policy always|missing|never` to control Docker image pulls.
`always` preserves the release-build default. Use `missing` for reproducible
offline-friendly iteration and `never` when the image must already exist.

## Behavior and safeguards

- The helper takes a non-blocking project lock. Never launch overlapping
  builds in the same project; a second invocation exits with the lock owner.
- Docker builds use an internal shadow copy and sync generated outputs back.
- Local SDK builds run as the project owner and default to `--no-vcs-apply`.
  Pass `--vcs-apply` only when that behavior is deliberately wanted.
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

## Troubleshooting

- If a local backend or exact target is requested and unavailable, report the
  installed target list from the helper error. Do not silently switch to an
  older mirror image.
- If a Docker image tag is absent, report the resolved tag and pull policy.
- Do not infer the current SailfishOS release or SDK target from available
  `coderus/sailfishos-platform-sdk` tags. Query installed target metadata or an
  authoritative Sailfish source for that separate question.
- If generated files leak between architectures, rerun with `--clean` and
  inspect the cleanup manifest under `.mb2`.
- Treat rpmlint warnings separately from build success and report generated
  RPM paths even when policy warnings exist.
- Prefer the machine-readable preflight and last-build JSON in integrations;
  avoid parsing human stderr when structured data is available.
