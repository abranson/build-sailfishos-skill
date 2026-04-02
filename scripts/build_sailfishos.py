#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


CONTAINER_UID = 100000
CONTAINER_IMAGE = "coderus/sailfishos-platform-sdk"
STATE_DIRNAME = "build-sailfishos-skill"
MANIFEST_NAME = "build-sailfishos-skill-manifest.txt"
BUILD_LOG_NAME = "build-sailfishos-skill-last.log"
BUILD_METADATA_NAME = "build-sailfishos-skill-last-build.json"
DEFAULT_PERMISSION_FALLBACK = "error"

ROOT_PATTERNS = (
    "Makefile",
    ".qmake.stash",
    "*.o",
    "*.a",
    "*.so",
    "*.prl",
    "*.list",
    "moc_*.cpp",
    "moc_*.o",
    "qrc_*.cpp",
    "qrc_*.o",
    "ui_*.h",
    "CMakeCache.txt",
    "cmake_install.cmake",
    "compile_commands.json",
    "build.ninja",
    "rules.ninja",
    "install_manifest.txt",
)

RECURSIVE_PATTERNS = (
    "**/Makefile",
    "**/.qmake.stash",
    "**/moc_*.cpp",
    "**/moc_*.o",
    "**/qrc_*.cpp",
    "**/qrc_*.o",
    "**/*.o",
    "**/*.a",
    "**/*.so",
    "**/*.prl",
    "**/*.list",
    "**/ui_*.h",
    "**/CMakeCache.txt",
    "**/cmake_install.cmake",
    "**/compile_commands.json",
    "**/build.ninja",
    "**/rules.ninja",
    "**/install_manifest.txt",
    "**/CMakeFiles",
)

ROOT_DIRS = (
    "installroot",
)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def run(cmd: list[str], cwd: Path | None = None, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def require_tool(name: str) -> None:
    if shutil.which(name):
        return
    raise SystemExit(f"Required tool not found: {name}")


def project_state_dir(project_dir: Path) -> Path:
    return project_dir / ".mb2" / STATE_DIRNAME


def manifest_path(project_dir: Path) -> Path:
    return project_dir / ".mb2" / MANIFEST_NAME


def build_log_path(project_dir: Path) -> Path:
    return project_dir / ".mb2" / BUILD_LOG_NAME


def build_metadata_path(project_dir: Path) -> Path:
    return project_dir / ".mb2" / BUILD_METADATA_NAME


def default_artifacts_dir(project_dir: Path) -> Path:
    return project_dir / "RPMS"


def staging_rpms_dir(project_dir: Path) -> Path:
    return project_state_dir(project_dir) / "rpms"


def has_spec_files(project_dir: Path) -> bool:
    rpm_dir = project_dir / "rpm"
    return rpm_dir.is_dir() and any(rpm_dir.glob("*.spec"))


def parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def is_version_release_tag(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+){3}", value))


def fetch_release_tags(prefix: str | None = None) -> list[str]:
    name_filter = quote(prefix) if prefix else ""

    matches: list[str] = []
    next_url = (
        "https://registry.hub.docker.com/v2/repositories/"
        f"{CONTAINER_IMAGE}/tags?page_size=100"
        f"{f'&name={name_filter}' if name_filter else ''}"
    )
    while next_url:
        with urlopen(next_url, timeout=10) as response:
            payload = json.load(response)
        for result in payload.get("results", []):
            name = result.get("name", "").strip()
            if prefix:
                if not (name == prefix or name.startswith(f"{prefix}.")):
                    continue
            if is_version_release_tag(name):
                matches.append(name)
        next_url = payload.get("next")
    return sorted(dict.fromkeys(matches), key=parse_version)


def latest_release_tag() -> str:
    matches = fetch_release_tags()
    if not matches:
        raise SystemExit(
            f"Could not determine the latest SailfishOS release from {CONTAINER_IMAGE} tags."
        )
    return matches[-1]


def normalize_release_tag(release: str) -> str:
    if release == "latest":
        try:
            resolved = latest_release_tag()
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit("Could not resolve SailfishOS release tag 'latest'") from exc
        log(f"Resolved SailfishOS release latest to {resolved}")
        return resolved

    if not re.fullmatch(r"\d+(?:\.\d+){2,3}", release):
        return release
    if release.count(".") >= 3:
        return release

    try:
        matches = fetch_release_tags(release)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return release

    if not matches:
        return release
    if release in matches:
        return release

    resolved = max(matches, key=parse_version)
    log(f"Resolved SailfishOS release {release} to {resolved}")
    return resolved


def infer_release_from_workflows(project_dir: Path) -> str | None:
    workflows_dir = project_dir / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return None

    regexes = (
        re.compile(r"^\s*RELEASE:\s*([^\s#]+)\s*$"),
        re.compile(rf"{re.escape(CONTAINER_IMAGE)}:([^\s'\"#]+)"),
    )

    for workflow in sorted(workflows_dir.glob("*.y*ml")):
        try:
            text = workflow.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            for regex in regexes:
                match = regex.search(line)
                if match:
                    return match.group(1).strip()
    return None


def resolve_release(project_dirs: Iterable[Path], explicit_release: str | None) -> str:
    if explicit_release:
        return normalize_release_tag(explicit_release)

    env_release = os.environ.get("SAILFISHOS_RELEASE")
    if env_release:
        return normalize_release_tag(env_release)

    seen: set[Path] = set()
    for project_dir in project_dirs:
        if project_dir in seen:
            continue
        seen.add(project_dir)
        inferred = infer_release_from_workflows(project_dir)
        if inferred:
            return normalize_release_tag(inferred)

    try:
        resolved = latest_release_tag()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "Could not determine SailfishOS release from arguments, environment, workflows, or Docker tags."
        ) from exc

    log(f"No SailfishOS release specified; using latest available release {resolved}")
    return resolved


def parse_last_arch(project_dir: Path) -> str | None:
    target_file = project_dir / ".mb2" / "target"
    if not target_file.is_file():
        return None

    target = target_file.read_text(encoding="utf-8").strip()
    if not target:
        return None

    if target.startswith("SailfishOS-"):
        return target.rsplit("-", 1)[-1]

    prefix = target.split(".", 1)[0].strip()
    return prefix or None


def pull_image(release: str) -> None:
    image = f"{CONTAINER_IMAGE}:{release}"
    log(f"Pulling {image}")
    run(["docker", "pull", image])


def list_supported_arches(release: str) -> list[str]:
    image = f"{CONTAINER_IMAGE}:{release}"
    result = run(
        [
            "docker",
            "run",
            "--rm",
            image,
            "bash",
            "-lc",
            "sb2-config -l",
        ],
        capture_output=True,
    )

    arches: list[str] = []
    seen: set[str] = set()
    prefix = f"SailfishOS-{release}-"
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            arch = line[len(prefix) :]
            if arch.endswith(".default") or arch in seen:
                continue
            arches.append(arch)
            seen.add(arch)
    if not arches:
        raise SystemExit(f"No supported architectures found in {image}")
    return arches


def resolve_arches(requested_arches: list[str], build_all: bool, supported_arches: list[str], project_dir: Path) -> list[str]:
    if build_all:
        return supported_arches

    if requested_arches:
        invalid = [arch for arch in requested_arches if arch not in supported_arches]
        if invalid:
            raise SystemExit(
                f"Unsupported architectures: {', '.join(invalid)}. Supported: {', '.join(supported_arches)}"
            )
        return requested_arches

    last_arch = parse_last_arch(project_dir)
    if last_arch and last_arch in supported_arches:
        return [last_arch]

    raise SystemExit(
        "No architecture was specified and .mb2/target did not contain a supported one. "
        f"Pass --arch or --all. Supported: {', '.join(supported_arches)}"
    )


def spec_names(project_dir: Path) -> set[str]:
    names: set[str] = set()
    for spec in sorted((project_dir / "rpm").glob("*.spec")):
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            match = re.match(r"^\s*Name:\s*(\S+)\s*$", line)
            if match:
                names.add(match.group(1))
                break
    return names


def pro_targets(project_dir: Path) -> set[str]:
    targets: set[str] = set()
    for pro in sorted(project_dir.glob("*.pro")):
        try:
            text = pro.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            match = re.match(r"^\s*TARGET\s*=\s*([^\s#]+)\s*$", line)
            if match and "$" not in match.group(1):
                targets.add(match.group(1))
                break
    return targets


def generated_candidate_paths(project_dir: Path) -> set[Path]:
    paths: set[Path] = set()

    for dirname in ROOT_DIRS:
        path = project_dir / dirname
        if path.exists():
            paths.add(path)

    for pattern in ROOT_PATTERNS:
        paths.update(path for path in project_dir.glob(pattern) if path.exists())

    for pattern in RECURSIVE_PATTERNS:
        paths.update(
            path
            for path in project_dir.glob(pattern)
            if path.exists() and ".git" not in path.parts and ".mb2" not in path.parts
        )

    for qm in (project_dir / "translations").glob("*.qm") if (project_dir / "translations").is_dir() else []:
        if qm.exists():
            paths.add(qm)

    for name in sorted(spec_names(project_dir) | pro_targets(project_dir)):
        candidate = project_dir / name
        if candidate.exists():
            paths.add(candidate)

    state_dir = project_state_dir(project_dir)
    if state_dir.exists():
        paths.discard(state_dir)

    return {path for path in paths if path.exists()}


def load_manifest(project_dir: Path) -> list[Path]:
    path = manifest_path(project_dir)
    if not path.is_file():
        return []

    result: list[Path] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = (project_dir / line).resolve()
        try:
            candidate.relative_to(project_dir.resolve())
        except ValueError:
            continue
        if candidate.exists():
            result.append(candidate)
    return result


def write_manifest(project_dir: Path, paths: Iterable[Path]) -> None:
    manifest = manifest_path(project_dir)
    manifest.parent.mkdir(parents=True, exist_ok=True)

    rel_paths = []
    root = project_dir.resolve()
    for path in sorted({p.resolve() for p in paths if p.exists()}):
        try:
            rel_paths.append(str(path.relative_to(root)))
        except ValueError:
            continue

    manifest.write_text("\n".join(rel_paths) + ("\n" if rel_paths else ""), encoding="utf-8")


def write_build_metadata(
    project_dir: Path,
    *,
    release: str,
    arch: str,
    debug_build: bool,
    artifacts_dir: Path,
    status: str,
    rpms: Iterable[Path],
) -> None:
    metadata_file = build_metadata_path(project_dir)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "release": release,
        "arch": arch,
        "debug": debug_build,
        "status": status,
        "artifacts_dir": str(artifacts_dir),
        "build_log": str(build_log_path(project_dir)),
        "rpms": [str(path) for path in rpms],
    }
    metadata_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_target_marker(project_dir: Path, arch: str) -> None:
    mb2_dir = project_dir / ".mb2"
    mb2_dir.mkdir(parents=True, exist_ok=True)
    (mb2_dir / "target").write_text(f"{arch}.{STATE_DIRNAME}\n", encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def cleanup_generated_artifacts(project_dir: Path, reason: str) -> list[Path]:
    manifest_paths = load_manifest(project_dir)
    cleanup_paths = set(manifest_paths) | generated_candidate_paths(project_dir)

    removed: list[Path] = []
    for path in sorted(cleanup_paths):
        if project_state_dir(project_dir) in path.parents or path == project_state_dir(project_dir):
            continue
        if path == manifest_path(project_dir):
            continue
        if path.exists():
            remove_path(path)
            removed.append(path)

    log(f"{reason}; removed {len(removed)} stale in-place artifacts")
    return removed


def cleanup_in_place_artifacts(project_dir: Path, previous_arch: str, next_arch: str) -> list[Path]:
    return cleanup_generated_artifacts(
        project_dir,
        f"Switched architecture from {previous_arch} to {next_arch}",
    )


def ensure_container_write_access(project_dir: Path, permission_fallback: str) -> None:
    current_uid = os.getuid()
    if shutil.which("setfacl"):
        log(
            f"Granting write ACLs to host uid {current_uid} and container uid {CONTAINER_UID} under {project_dir}"
        )
        run(
            [
                "find",
                str(project_dir),
                "-uid",
                str(current_uid),
                "-exec",
                "setfacl",
                "-m",
                f"u:{current_uid}:rwX,u:{CONTAINER_UID}:rwX",
                "{}",
                "+",
            ]
        )
        run(
            [
                "find",
                str(project_dir),
                "-type",
                "d",
                "-uid",
                str(current_uid),
                "-exec",
                "setfacl",
                "-m",
                f"d:u:{current_uid}:rwX,d:u:{CONTAINER_UID}:rwX",
                "{}",
                "+",
            ]
        )
        return

    if permission_fallback == "chmod":
        log("setfacl unavailable; falling back to chmod -R a+rwX")
        run(["chmod", "-R", "a+rwX", str(project_dir)])
        return

    raise SystemExit(
        "setfacl is unavailable, so the Docker container may not be able to write in place. "
        "Install acl utilities or rerun with --permission-fallback chmod."
    )


def parse_missing_build_requires(log_text: str) -> list[str]:
    missing: list[str] = []
    capture = False
    for line in log_text.splitlines():
        if line.strip() == "error: Failed build dependencies:":
            capture = True
            continue
        if not capture:
            continue
        if line.startswith("\t") or line.startswith("    "):
            requirement = line.strip()
            if " is needed by " in requirement:
                requirement = requirement.split(" is needed by ", 1)[0].strip()
            if requirement:
                missing.append(requirement)
            continue
        if missing and line.strip():
            break
    return sorted(dict.fromkeys(missing))


def extract_zypper_names(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        if "|" not in line or line.lstrip().startswith("--+"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        name = parts[1]
        if name and name not in {"Name", "S"}:
            names.append(name)
    return sorted(dict.fromkeys(names))


def diagnose_missing_dependencies(project_dir: Path, release: str, arch: str) -> None:
    log_file = build_log_path(project_dir)
    if not log_file.is_file():
        return

    missing = parse_missing_build_requires(log_file.read_text(encoding="utf-8"))
    if not missing:
        return

    image = f"{CONTAINER_IMAGE}:{release}"
    target = f"SailfishOS-{release}-{arch}"
    log("Dependency diagnostics from the target SDK:")
    for requirement in missing:
        if requirement.startswith("pkgconfig("):
            query = (
                f"sb2 -t {shlex.quote(target)} -m sdk-install -R "
                f"zypper search --provides --match-exact {shlex.quote(requirement)}"
            )
        else:
            query = (
                f"sb2 -t {shlex.quote(target)} -m sdk-install -R "
                f"zypper se -s {shlex.quote(requirement)}"
            )

        try:
            result = run(
                ["docker", "run", "--rm", image, "bash", "-lc", query],
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            log(f"- {requirement}: diagnostic lookup failed")
            continue

        names = extract_zypper_names(result.stdout)
        if names:
            log(f"- {requirement}: available as {', '.join(names)}")
        else:
            log(f"- {requirement}: not available in {target}")


def verify_expected_rpms(rpms: list[Path], debug_build: bool) -> None:
    if not rpms:
        raise SystemExit("Build completed but no RPMs were captured")
    if debug_build:
        names = {rpm.name for rpm in rpms}
        if not any("-debuginfo-" in name for name in names):
            raise SystemExit("Debug build completed but no -debuginfo RPM was produced")
        if not any("-debugsource-" in name for name in names):
            raise SystemExit("Debug build completed but no -debugsource RPM was produced")


def variant_destination_dir(base_dir: Path, release: str, arch: str, debug_build: bool) -> Path:
    return base_dir / release / arch / ("debug" if debug_build else "release")


def build_arch(project_dir: Path, release: str, arch: str, debug_build: bool = False) -> None:
    image = f"{CONTAINER_IMAGE}:{release}"
    target = f"SailfishOS-{release}-{arch}"
    binary_names = ":".join(sorted(spec_names(project_dir) | pro_targets(project_dir)))
    command = r'''
set -euo pipefail
workroot="${HOME:-/tmp}"
if [ ! -d "$workroot" ] || [ ! -w "$workroot" ]; then
  workroot=/tmp
fi
workdir="$workroot/build-sailfishos-skill"
mkdir -p /share/.mb2
mkdir -p /share/.mb2/build-sailfishos-skill
logfile=/share/.mb2/build-sailfishos-skill-last.log
: > "$logfile"
{
  echo "# build-sailfishos-skill"
  echo "release=${RELEASE:-}"
  echo "arch=${ARCH:-}"
  echo "debug=${DEBUG_BUILD:-0}"
  echo "target=${TARGET:-}"
  echo
} >> "$logfile"
rm -rf "$workdir"
mkdir -p "$workdir"
cp -a /share/. "$workdir/"
rm -rf "$workdir/RPMS"
cd "$workdir"
mb2_args=( -t "$TARGET" build )
if [ "${DEBUG_BUILD:-0}" = "1" ]; then
  mb2_args+=( -d )
fi
mb2 "${mb2_args[@]}" 2>&1 | tee -a "$logfile"

for state_file in .mb2/target .mb2/spec .mb2/snapshot.lock; do
  if [ -e "$state_file" ]; then
    cp -f "$state_file" /share/.mb2/
  fi
done
chmod -R a+rwX /share/.mb2 >/dev/null 2>&1 || true

rm -rf /share/.mb2/build-sailfishos-skill/rpms
if [ -d RPMS ]; then
  mkdir -p /share/.mb2/build-sailfishos-skill/rpms
  find RPMS -maxdepth 1 -type f -name '*.rpm' -exec cp -f {} /share/.mb2/build-sailfishos-skill/rpms/ \;
  chmod -R a+rwX /share/.mb2/build-sailfishos-skill/rpms >/dev/null 2>&1 || true
fi

for pattern in \
  Makefile .qmake.stash '*.o' '*.a' '*.so' '*.prl' '*.list' \
  'moc_*.cpp' 'moc_*.o' 'qrc_*.cpp' 'qrc_*.o' 'ui_*.h' \
  CMakeCache.txt cmake_install.cmake compile_commands.json build.ninja rules.ninja install_manifest.txt
do
  for f in $pattern; do
    [ -e "$f" ] || continue
    cp -f "$f" /share/
  done
done

if [ -d translations ]; then
  mkdir -p /share/translations
  for f in translations/*.qm; do
    [ -e "$f" ] || continue
    cp -f "$f" /share/translations/
  done
fi

OLDIFS="$IFS"
IFS=':'
for name in ${SYNC_BINARIES:-}; do
  [ -n "$name" ] || continue
  [ -e "$name" ] || continue
  cp -f "$name" /share/
done
IFS="$OLDIFS"
'''
    log(f"Building {target} via container shadow build and syncing artifacts back in place")
    try:
        run(
            [
                "docker",
                "run",
                "--rm",
                "--privileged",
                "-v",
                f"{project_dir}:/share",
                "-e",
                f"TARGET={target}",
                "-e",
                f"RELEASE={release}",
                "-e",
                f"ARCH={arch}",
                "-e",
                f"DEBUG_BUILD={'1' if debug_build else '0'}",
                "-e",
                f"BUILD_LOG={build_log_path(project_dir)}",
                "-e",
                f"SYNC_BINARIES={binary_names}",
                image,
                "bash",
                "-lc",
                command,
            ]
        )
    except subprocess.CalledProcessError:
        diagnose_missing_dependencies(project_dir, release, arch)
        raise


def copy_rpms(project_dir: Path, release: str, arch: str, debug_build: bool, artifacts_dir: Path) -> list[Path]:
    rpm_dir = staging_rpms_dir(project_dir)
    if not rpm_dir.is_dir():
        raise SystemExit("Build completed but staged RPMs were not captured")

    rpms = sorted(rpm_dir.glob("*.rpm"))
    if not rpms:
        raise SystemExit("Build completed but no staged RPMs were found")

    destination_dir = variant_destination_dir(artifacts_dir, release, arch, debug_build)
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for rpm in rpms:
        destination = destination_dir / rpm.name
        shutil.copy2(rpm, destination)
        copied.append(destination)

    shutil.rmtree(rpm_dir)
    return copied


def resolve_project_dir(project_dir: Path) -> Path:
    if not project_dir.is_dir():
        raise SystemExit(f"Project directory not found: {project_dir}")

    if has_spec_files(project_dir):
        return project_dir

    matches = [child for child in sorted(project_dir.iterdir()) if child.is_dir() and has_spec_files(child)]
    if len(matches) == 1:
        log(f"Using SailfishOS build root {matches[0]} discovered under {project_dir}")
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(str(path) for path in matches)
        raise SystemExit(
            f"{project_dir} contains multiple one-level-deep SailfishOS build roots: {options}. "
            "Pass --project-dir pointing at the intended one."
        )

    raise SystemExit(
        f"Could not find rpm/*.spec in {project_dir} or one level below it. "
        "Pass --project-dir pointing at the SailfishOS build root."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a SailfishOS project in place with Docker and mb2")
    parser.add_argument("--project-dir", default=".", help="Project root containing rpm/*.spec")
    parser.add_argument("--release", help="SailfishOS release, for example 3.4.0.24")
    parser.add_argument("--arch", action="append", default=[], help="Architecture to build, may be repeated")
    parser.add_argument("--all", action="store_true", help="Build every architecture supported by the chosen SDK image")
    parser.add_argument("--list-arches", action="store_true", help="Print supported architectures and exit")
    parser.add_argument(
        "--permission-fallback",
        choices=("error", "chmod"),
        default=DEFAULT_PERMISSION_FALLBACK,
        help="Fallback when setfacl is unavailable",
    )
    parser.add_argument(
        "--artifacts-dir",
        help="Directory where built RPMs are copied. Defaults to RPMS/",
    )
    parser.add_argument("--clean", action="store_true", help="Remove generated in-place build artifacts before building")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Pass -d to mb2 build so main binaries are stripped and debug packages are generated",
    )
    parser.add_argument("--no-pull", action="store_true", help="Skip docker pull before building")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_tool("docker")

    requested_project_dir = Path(args.project_dir).resolve()
    project_dir = resolve_project_dir(requested_project_dir)

    release = resolve_release((requested_project_dir, project_dir), args.release)
    if not args.no_pull:
        pull_image(release)

    supported_arches = list_supported_arches(release)

    if args.list_arches:
        print("\n".join(supported_arches))
        return 0

    arches = resolve_arches(args.arch, args.all, supported_arches, project_dir)
    artifacts_dir = Path(args.artifacts_dir).resolve() if args.artifacts_dir else default_artifacts_dir(project_dir)

    ensure_container_write_access(project_dir, args.permission_fallback)

    all_copied_rpms: list[Path] = []
    for arch in arches:
        previous_arch = parse_last_arch(project_dir)
        if previous_arch and previous_arch != arch:
            cleanup_in_place_artifacts(project_dir, previous_arch, arch)
        elif args.clean:
            cleanup_generated_artifacts(project_dir, "Explicit cleanup requested")

        try:
            build_arch(project_dir, release, arch, debug_build=args.debug)
            write_target_marker(project_dir, arch)

            manifest_paths = generated_candidate_paths(project_dir)
            write_manifest(project_dir, manifest_paths)

            copied = copy_rpms(project_dir, release, arch, args.debug, artifacts_dir)
            verify_expected_rpms(copied, args.debug)
            write_build_metadata(
                project_dir,
                release=release,
                arch=arch,
                debug_build=args.debug,
                artifacts_dir=artifacts_dir,
                status="success",
                rpms=copied,
            )
            all_copied_rpms.extend(copied)
            log(
                f"Copied {len(copied)} RPM(s) for {arch} to "
                f"{variant_destination_dir(artifacts_dir, release, arch, args.debug)}"
            )
        except Exception:
            write_build_metadata(
                project_dir,
                release=release,
                arch=arch,
                debug_build=args.debug,
                artifacts_dir=artifacts_dir,
                status="failed",
                rpms=[],
            )
            raise

    print("Built RPMs:")
    for rpm in all_copied_rpms:
        print(rpm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
