---
name: build-sailfishos
description: Use when asked to build a SailfishOS project, validate packaging, produce RPMs for a specific SailfishOS release and architecture from Docker, or use an installed `/srv/mer` devel SDK target. For normal releases this skill pulls `coderus/sailfishos-platform-sdk:<release>`; for devel it uses a privileged Docker wrapper around the local SDK chroot instead of launching `sdk-chroot` directly from Codex. It checks `.mb2/target`, removes stale in-place artifacts before switching architectures, and archives RPMs under `RPMS/` by default.
---

# Build SailfishOS

Use the bundled script instead of retyping Docker and `mb2` commands.

In the example commands below, replace `<skill-dir>` with the actual path to this skill checkout or installed skill directory.

## Workflow

1. Confirm the supplied path is either the SailfishOS build root or a repo root with exactly one one-level-deep subdirectory containing `rpm/*.spec`.
2. Choose a SailfishOS release.
   Prefer an explicit user-provided release.
   Otherwise the script can infer from `SAILFISHOS_RELEASE` or a workflow file that contains `RELEASE:`.
   If none is provided anywhere, the helper falls back to the newest concrete SDK tag it can find on Docker Hub.
3. Run `scripts/build_sailfishos.py` from the SailfishOS build root or pass `--project-dir`. If the passed directory does not contain `rpm/*.spec`, the helper checks one level deep for a unique SailfishOS build root such as `sailfish/`.
4. Report the built RPM paths from `RPMS/<release>/<arch>/<release|debug>/`.
   If the user explicitly passed `--artifacts-dir`, also report the copied RPM paths there.

## Local Devel SDK

Use the installed SDK under `/srv/mer` when the user asks for `devel`, when they name an installed target such as `aarch64`, or when the newest public Docker SDK tag is too old for the package being tested.
Pass the target architecture to `mb2` without a snapshot suffix, for example `-t aarch64`; `mb2` automatically selects the appropriate snapshot.

The Docker Hub flow above is release-image based. It is not a substitute for an installed devel target: a public image such as `coderus/sailfishos-platform-sdk:5.0.0.43` can have an older userspace than the local devel target, and direct `mb2` use inside an old image can fail before the build starts, for example because the devel target compiler requires newer `glibc` symbols.

For Codex, use the privileged Docker wrapper first. Do not try to launch
`/srv/mer/sdks/sfossdk/sdk-chroot` directly from the host: it needs sudo-level
privileges and fails in normal sandboxed Codex runs.

Docker is used only as a privileged wrapper around the installed SDK. Mount `/srv/mer` and the project home, then enter `/srv/mer/sdks/sfossdk/sdk-chroot` from inside the container. Use a local SDK build-engine image if one exists, such as `sailfish-sdk-build-engine:<user>`.

```bash
docker run --rm --privileged \
  -v /srv/mer:/srv/mer \
  -v /home/$USER:/home/$USER \
  -w /path/to/project \
  sailfish-sdk-build-engine:$USER \
  bash -lc '
    set -euo pipefail
    sed -i "s#^mersdk:[^:]*:[0-9]*:[0-9]*:[^:]*:[^:]*:#'"$USER"':x:$(id -u):$(id -g)::/home/'"$USER"':#" /etc/passwd
    /srv/mer/sdks/sfossdk/sdk-chroot -u '"$USER"' bash -lc '"'"'
      set -o pipefail
      cd /path/to/project
      mkdir -p .mb2
      mb2 -t aarch64 --no-vcs-apply build --prepare -d 2>&1 | tee .mb2/build-sailfishos-devel-last.log
      exit ${PIPESTATUS[0]}
    '"'"'
  '
```

The `/etc/passwd` rewrite is ephemeral inside the wrapper container. It is needed when the image only has a `mersdk` user but the SDK chroot should run as the real project owner so build artifacts remain writable in the host checkout.
Use `--no-vcs-apply` for already-patched or dirty local source trees where `mb2` must not try to apply VCS state itself.

For RPM packaging trees where the source checkout is already patched, temporarily disable `%autosetup` patch application for local iteration and restore the spec afterward:

```bash
spec=rpm/package.spec
backup=$(mktemp)
cp -a "$spec" "$backup"
trap 'cp -a "$backup" "$spec"; rm -f "$backup"' EXIT
sed -i -e 's/^%autosetup -p1 -n /%autosetup -N -n /' "$spec"
# run the local devel mb2 command here
```

## Behavior

- The host project tree ends up with in-place build artifacts, but the Docker container performs the actual `mb2` build in an internal shadow copy and syncs the generated outputs back afterward. This avoids the direct bind-mount `mb2 build` failure mode.
- If `--project-dir` points at a repo root rather than the SailfishOS packaging root, the helper looks one level deep for a unique subdirectory containing `rpm/*.spec`. If it finds more than one, it stops and asks for an explicit build root.
- Before each build, the script reads `.mb2/target` and extracts the last built architecture.
- If the requested architecture differs, the script removes stale in-place build artifacts before rebuilding, including recursive qmake-generated files like subdirectory `Makefile`s and `.qmake.stash` files.
- The script keeps a cleanup manifest in `.mb2/build-sailfishos-skill-manifest.txt` and supplements it with a fresh scan of common qmake/CMake Sailfish build artifacts.
- `RPMS/` is the default artifact destination. The helper archives RPMs under `RPMS/<release>/<arch>/<release|debug>/` so multi-arch or debug builds do not overwrite each other.
- The current build's raw RPM output is staged under `.mb2/build-sailfishos-skill/rpms` and then archived into `RPMS/`. Treat that `.mb2` location as transient state only.
- The script grants the Docker container user write access with `setfacl` when available. If `setfacl` is unavailable, rerun with `--permission-fallback chmod` only if changing permissions broadly is acceptable.
- Newer SDK images may use `/home/mersdk` rather than `/home/nemo`; the helper chooses a writable container home automatically and falls back to `/tmp` when needed.
- Passing `--debug` forwards `-d` to `mb2 build`, which enables stripped main binaries plus `-debuginfo` and `-debugsource` RPM generation.
- If the user asks for "stripped binaries" or "stripped RPMs", interpret that as a debug build with `--debug`, because that is the Sailfish packaging mode that strips the main RPM payload and emits debug packages separately.
- The helper writes the last full build log to `.mb2/build-sailfishos-skill-last.log` and the last build metadata to `.mb2/build-sailfishos-skill-last-build.json`.
- The helper accepts shorthand release requests like `4.5.0` or `5.0.0`, and it also accepts `latest`. All of these resolve to the newest matching concrete SDK tag on Docker Hub.
- If no release is given in arguments, environment, or workflow files, the helper uses the newest concrete SDK tag it can find.
- On failed `BuildRequires`, the helper inspects the saved log and runs target-side `zypper` lookups to separate truly missing dependencies from packages that exist under different provider names.

## Commands

Build the last architecture recorded in `.mb2/target`:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir .
```

Build with the newest available SailfishOS release automatically:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --arch i486
```

Build a specific release and architecture:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --release 3.4.0.24 --arch aarch64
```

Build every architecture supported by the chosen SDK image:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --release 3.4.0.24 --all
```

Build with debug packages:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --release 5.0.0 --arch i486 --debug
```

Build stripped main RPMs for all architectures:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --all --debug
```

List the supported architectures without building:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --release 3.4.0.24 --list-arches
```

Force a generated-artifact cleanup before rebuilding:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --project-dir . --release 5.0.0 --arch i486 --clean
```

## Notes

- If the user asks to “build this SailfishOS project”, prefer the recorded `.mb2/target` architecture when present.
- If the user asks to use `devel`, prefer the installed `/srv/mer` SDK through
  the privileged Docker wrapper over resolving a public Docker SDK release or
  launching `sdk-chroot` directly from Codex.
- In multi-target repositories, it is fine to point `--project-dir` at the repo root when there is exactly one one-level-deep SailfishOS packaging directory.
- If the project tree is not writable by the Docker container user and `setfacl` is unavailable, stop and explain the permission issue instead of guessing.
- Keep cleanup limited to generated in-place build artifacts. Do not delete source files or unrelated untracked files.
- Prefer reporting RPMs from `RPMS/`. Treat `.mb2` as build state, not the package output location.
- If the user asks for stripped binaries, prefer `--debug` even if they did not explicitly mention debug packages.

## Troubleshooting

- If switching architectures causes compiler flags or qmake output from the old target to leak into the next build, rerun with `--clean`. The helper already removes known qmake and CMake outputs on arch changes, including subdirectory `Makefile`s and `.qmake.stash`.
- If the repo root does not contain `rpm/*.spec`, the helper checks one level down. If there are multiple matching subdirectories, pass `--project-dir` pointing directly at the intended SailfishOS build root.
- If a user says `4.5.0`, `5.0.0`, or `latest`, pass that directly. The helper resolves it to a concrete SDK tag like `4.5.0.16` or `5.0.0.43`.
- If no release is given anywhere, the helper queries Docker Hub and uses the newest concrete SDK tag it finds.
- If `mb2` fails on `BuildRequires`, inspect `.mb2/build-sailfishos-skill-last.log` and use the helper's dependency diagnostics in the stderr output. Report which requirements are truly unavailable in that SDK and which ones exist under different package names.
- `rpmlint` warnings do not necessarily mean the build failed. Report the generated RPMs separately from any policy warnings.
- Use `.mb2/build-sailfishos-skill-last-build.json` for a machine-readable summary of the last release, arch, debug mode, status, and RPM paths.
