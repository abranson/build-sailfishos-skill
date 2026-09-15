import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_sailfishos.py"
SPEC = importlib.util.spec_from_file_location("build_sailfishos", SCRIPT)
assert SPEC and SPEC.loader
build_sailfishos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_sailfishos)


class BuildSailfishOsTests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        project = root / "project"
        (project / "rpm").mkdir(parents=True)
        (project / "rpm" / "example.spec").write_text("Name: example\n", encoding="utf-8")
        return project

    def test_parser_exposes_backend_and_safety_controls(self):
        args = build_sailfishos.parse_args(
            [
                "--backend",
                "local",
                "--target",
                "aarch64",
                "--pull-policy",
                "missing",
                "--no-vcs-apply",
                "--allow-untrusted-rpms",
                "--snapshot-key",
                "browser-esr153",
                "--snapshot-repository",
                "browser=https://example.invalid/browser",
                "--snapshot-package",
                "qtmozembed-qt5-devel",
                "--dry-run",
                "--json",
                "--quiet",
            ]
        )

        self.assertEqual(args.backend, "local")
        self.assertEqual(args.target, ["aarch64"])
        self.assertEqual(args.pull_policy, "missing")
        self.assertTrue(args.no_vcs_apply)
        self.assertTrue(args.allow_untrusted_rpms)
        self.assertEqual(args.snapshot_key, "browser-esr153")
        self.assertEqual(
            args.snapshot_repository,
            ["browser=https://example.invalid/browser"],
        )
        self.assertEqual(args.snapshot_package, ["qtmozembed-qt5-devel"])
        self.assertTrue(args.quiet)

    def test_default_local_sdk_uses_first_executable_candidate(self):
        primary = Path("/srv/mer/sdks/sfossdk/sdk-chroot")
        alternate = Path("/srv/sfos/sdks/sdk/sdk-chroot")

        def is_executable(path, mode):
            return path == alternate and mode == build_sailfishos.os.X_OK

        with (
            mock.patch.object(
                build_sailfishos,
                "LOCAL_SDK_CANDIDATES",
                (primary, alternate),
            ),
            mock.patch.object(
                build_sailfishos.os,
                "access",
                side_effect=is_executable,
            ),
        ):
            selected = build_sailfishos.default_local_sdk()

        self.assertEqual(selected, alternate)

    def test_local_sdk_mount_root_finds_targets_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdks" / "sdk" / "sdk-chroot"
            sdk.parent.mkdir(parents=True)
            sdk.touch()
            (root / "targets").mkdir()

            self.assertEqual(build_sailfishos.local_sdk_mount_root(sdk), root)

    def test_snapshot_key_groups_work_by_esr_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            esr_140 = self.make_project(root / ".codex-webview-esr140-worktree")
            esr_153 = self.make_project(root / ".codex-gecko-esr153-worktree")

            self.assertEqual(build_sailfishos.infer_snapshot_key(esr_140), "browser-esr140")
            self.assertEqual(build_sailfishos.infer_snapshot_key(esr_153), "browser-esr153")

    def test_snapshot_key_falls_back_to_stable_package_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))

            self.assertEqual(build_sailfishos.infer_snapshot_key(project), "example")

    def test_snapshot_plan_reuses_one_named_environment(self):
        builds = [build_sailfishos.LocalSdkBuild("aarch64", "aarch64")]
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            key, planned = build_sailfishos.plan_local_sdk_snapshots(
                builds,
                project,
                "browser-esr153",
                has_custom_inputs=False,
            )

        self.assertEqual(key, "browser-esr153")
        self.assertEqual(
            planned,
            [
                build_sailfishos.LocalSdkBuild(
                    "aarch64",
                    "aarch64",
                    "aarch64-browser-esr153",
                )
            ],
        )

    def test_snapshot_plan_shares_base_default_without_custom_inputs(self):
        builds = [build_sailfishos.LocalSdkBuild("aarch64", "aarch64")]
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            key, planned = build_sailfishos.plan_local_sdk_snapshots(
                builds,
                project,
                None,
                has_custom_inputs=False,
            )

        self.assertIsNone(key)
        self.assertEqual(
            planned,
            [
                build_sailfishos.LocalSdkBuild(
                    "aarch64",
                    "aarch64",
                    "aarch64",
                )
            ],
        )

    def test_snapshot_plan_isolates_implicit_custom_inputs(self):
        builds = [build_sailfishos.LocalSdkBuild("aarch64", "aarch64")]
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            key, planned = build_sailfishos.plan_local_sdk_snapshots(
                builds,
                project,
                None,
                has_custom_inputs=True,
            )

        self.assertEqual(key, "example")
        self.assertEqual(planned[0].snapshot, "aarch64-example")

    def test_snapshot_target_marker_still_resolves_architecture(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            (project / ".mb2").mkdir()
            (project / ".mb2" / "target").write_text(
                "aarch64-browser-esr153.default\n",
                encoding="utf-8",
            )

            self.assertEqual(build_sailfishos.parse_last_arch(project), "aarch64")

    def test_snapshot_repository_recipe_has_stable_alias(self):
        repositories = build_sailfishos.parse_snapshot_repositories(
            [
                "browser=https://example.invalid/browser",
                "https://example.invalid/extra",
                "https://example.invalid/private?token=opaque",
            ]
        )

        self.assertEqual(repositories[0].alias, "browser")
        self.assertEqual(repositories[0].url, "https://example.invalid/browser")
        self.assertTrue(repositories[1].alias.startswith("build-sailfishos-"))
        self.assertEqual(
            repositories[2].url,
            "https://example.invalid/private?token=opaque",
        )

    def test_quiet_run_suppresses_success_output(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        previous = build_sailfishos._QUIET_OUTPUT
        build_sailfishos._QUIET_OUTPUT = True
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = build_sailfishos.run(
                    [sys.executable, "-c", "print('large successful output')"]
                )
        finally:
            build_sailfishos._QUIET_OUTPUT = previous

        self.assertEqual(result.returncode, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_quiet_run_reports_bounded_failure_tail(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        previous = build_sailfishos._QUIET_OUTPUT
        build_sailfishos._QUIET_OUTPUT = True
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(subprocess.CalledProcessError):
                    build_sailfishos.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import sys; "
                                "print('noise' * 20000); "
                                "print('final useful error'); "
                                "sys.exit(7)"
                            ),
                        ]
                    )
        finally:
            build_sailfishos._QUIET_OUTPUT = previous

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("final useful error", stderr.getvalue())
        self.assertLess(len(stderr.getvalue()), 7000)

    def test_local_sdk_rpms_are_staged_inside_project(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            project = self.make_project(root)
            external = root / "external-rpms"
            external.mkdir()
            wanted = external / "dependency-1-1.aarch64.rpm"
            wanted.write_bytes(b"rpm")
            (external / "dependency-debuginfo-1-1.aarch64.rpm").write_bytes(b"debug")
            selected = build_sailfishos.validate_local_rpm_dirs([external])

            with build_sailfishos.stage_local_sdk_rpms(project, selected) as staged:
                self.assertEqual(len(staged), 1)
                self.assertEqual([path.name for path in staged[0].glob("*.rpm")], [wanted.name])
                self.assertTrue(staged[0].is_relative_to(project))

            self.assertFalse(build_sailfishos.local_rpms_staging_dir(project).exists())

    def test_project_lock_rejects_a_second_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with build_sailfishos.project_build_lock(project):
                with self.assertRaisesRegex(SystemExit, "already active"):
                    with build_sailfishos.project_build_lock(project):
                        pass

    def test_exact_local_target_selection(self):
        target = build_sailfishos.LocalSdkTarget(
            arch="aarch64",
            target="aarch64-devel",
            release="devel",
            version_id="6.0.0.0",
            flavour="devel",
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with mock.patch.object(build_sailfishos, "list_local_sdk_targets", return_value=[target]):
                selected = build_sailfishos.select_local_sdk_builds(
                    Path("/srv/mer/sdks/sfossdk/sdk-chroot"),
                    "live",
                    [],
                    False,
                    project,
                    ["aarch64-devel.default"],
                )

        self.assertEqual(selected, [build_sailfishos.LocalSdkBuild("aarch64", "aarch64-devel")])

    def test_local_target_selection_ignores_project_snapshots(self):
        base = build_sailfishos.LocalSdkTarget(
            arch="aarch64",
            target="aarch64",
            release="live",
            version_id="5.3.0.10",
            flavour="devel",
        )
        snapshot = build_sailfishos.LocalSdkTarget(
            arch="aarch64",
            target="aarch64-browser-esr153",
            release="live",
            version_id="5.3.0.10",
            flavour="devel",
            snapshot_of="aarch64",
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with mock.patch.object(
                build_sailfishos,
                "list_local_sdk_targets",
                return_value=[snapshot, base],
            ):
                selected = build_sailfishos.select_local_sdk_builds(
                    Path("/srv/mer/sdks/sfossdk/sdk-chroot"),
                    "live",
                    ["aarch64"],
                    False,
                    project,
                )

        self.assertEqual(selected, [build_sailfishos.LocalSdkBuild("aarch64", "aarch64")])

    def test_local_target_selection_requires_registered_base(self):
        target = build_sailfishos.LocalSdkTarget(
            arch="aarch64",
            target="aarch64",
            release="live",
            version_id="5.3.0.10",
            flavour="devel",
            registered=False,
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with mock.patch.object(
                build_sailfishos,
                "list_local_sdk_targets",
                return_value=[target],
            ):
                selected = build_sailfishos.select_local_sdk_builds(
                    Path("/srv/mer/sdks/sfossdk/sdk-chroot"),
                    "live",
                    ["aarch64"],
                    False,
                    project,
                )

        self.assertIsNone(selected)

    def test_local_build_prepares_and_uses_snapshot_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with (
                mock.patch.object(build_sailfishos, "host_user", return_value="builder"),
                mock.patch.object(
                    build_sailfishos,
                    "local_sdk_project_mount_root",
                    return_value=Path("/workspace"),
                ),
                mock.patch.object(
                    build_sailfishos,
                    "local_sdk_mount_root",
                    return_value=Path("/srv/mer"),
                ),
                mock.patch.object(build_sailfishos, "run") as run,
            ):
                build_sailfishos.build_local_sdk_arch(
                    project,
                    Path("/srv/mer/sdks/sfossdk/sdk-chroot"),
                    "5.3.0.10",
                    "aarch64",
                    "aarch64",
                    "aarch64-browser-esr153",
                    snapshot_repositories=[
                        build_sailfishos.SnapshotRepository(
                            "browser",
                            "https://example.invalid/browser",
                        )
                    ],
                    snapshot_packages=["qtmozembed-qt5-devel"],
                )

        command = run.call_args.args[0]
        command_text = " ".join(command)
        self.assertIn("BASE_TARGET=aarch64", command)
        self.assertIn("TARGET=aarch64-browser-esr153", command)
        self.assertIn("SNAPSHOT_REPOSITORIES=browser\thttps://example.invalid/browser", command)
        self.assertIn("SNAPSHOT_PACKAGES=qtmozembed-qt5-devel", command)
        self.assertIn("sdk-manage target snapshot --reset=outdated", command_text)
        self.assertIn('if [ "$TARGET" != "$BASE_TARGET" ]; then', command_text)
        self.assertIn('mb2_args=( -t "$TARGET" )', command_text)
        self.assertNotIn("SNAPSHOT_ROOT=", command_text)
        self.assertNotIn('sb2 -t "$BASE_TARGET"', command_text)

    def test_local_build_normalizes_nested_default_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with (
                mock.patch.object(build_sailfishos, "host_user", return_value="builder"),
                mock.patch.object(
                    build_sailfishos,
                    "local_sdk_project_mount_root",
                    return_value=Path("/workspace"),
                ),
                mock.patch.object(
                    build_sailfishos,
                    "local_sdk_mount_root",
                    return_value=Path("/srv/mer"),
                ),
                mock.patch.object(build_sailfishos, "run") as run,
            ):
                build_sailfishos.build_local_sdk_arch(
                    project,
                    Path("/srv/mer/sdks/sfossdk/sdk-chroot"),
                    "5.3.0.10",
                    "aarch64",
                    "aarch64.default",
                    "aarch64-example.default.default",
                )

        command = run.call_args.args[0]
        command_text = " ".join(command)
        self.assertIn("BASE_TARGET=aarch64", command)
        self.assertIn("TARGET=aarch64-example", command)
        self.assertNotIn("SNAPSHOT_ROOT=", command_text)
        self.assertNotIn("TARGET=aarch64-example.default", command)
        self.assertNotIn("TARGET=aarch64-example.default.default", command)

    def test_dry_run_does_not_mutate_project_or_pull(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            stdout = io.StringIO()
            with (
                mock.patch.object(build_sailfishos, "require_tool"),
                mock.patch.object(build_sailfishos, "docker_image_exists", return_value=False),
                mock.patch.object(build_sailfishos, "ensure_image") as ensure_image,
                redirect_stdout(stdout),
            ):
                result = build_sailfishos.main(
                    [
                        "--project-dir",
                        str(project),
                        "--release",
                        "5.0.0.43",
                        "--arch",
                        "aarch64",
                        "--dry-run",
                        "--json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(payload["backend"], "docker")
            self.assertTrue(payload["would_pull"])
            self.assertFalse(payload["mutates_project"])
            ensure_image.assert_not_called()
            self.assertFalse((project / ".mb2").exists())

    def test_local_dry_run_reports_base_and_managed_snapshot(self):
        target = build_sailfishos.LocalSdkTarget(
            arch="aarch64",
            target="aarch64",
            release="live",
            version_id="5.3.0.10",
            flavour="devel",
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            stdout = io.StringIO()
            with (
                mock.patch.object(build_sailfishos, "require_tool"),
                mock.patch.object(
                    build_sailfishos,
                    "list_local_sdk_targets",
                    return_value=[target],
                ),
                mock.patch.object(build_sailfishos, "docker_image_exists", return_value=True),
                mock.patch.object(build_sailfishos, "docker_image_id", return_value="image-id"),
                redirect_stdout(stdout),
            ):
                result = build_sailfishos.main(
                    [
                        "--project-dir",
                        str(project),
                        "--backend",
                        "local",
                        "--arch",
                        "aarch64",
                        "--snapshot-key",
                        "browser-esr153",
                        "--snapshot-repository",
                        "browser=https://example.invalid/browser",
                        "--snapshot-package",
                        "qtmozembed-qt5-devel",
                        "--dry-run",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["builds"][0]["base_target"], "aarch64")
        self.assertEqual(
            payload["builds"][0]["target"],
            "aarch64-browser-esr153",
        )
        self.assertEqual(payload["snapshot_key"], "browser-esr153")
        self.assertEqual(payload["snapshot_packages"], ["qtmozembed-qt5-devel"])

    def test_local_dry_run_uses_shared_default_without_custom_inputs(self):
        target = build_sailfishos.LocalSdkTarget(
            arch="aarch64",
            target="aarch64",
            release="live",
            version_id="5.3.0.10",
            flavour="devel",
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            stdout = io.StringIO()
            with (
                mock.patch.object(build_sailfishos, "require_tool"),
                mock.patch.object(
                    build_sailfishos,
                    "list_local_sdk_targets",
                    return_value=[target],
                ),
                mock.patch.object(build_sailfishos, "docker_image_exists", return_value=True),
                mock.patch.object(build_sailfishos, "docker_image_id", return_value="image-id"),
                redirect_stdout(stdout),
            ):
                result = build_sailfishos.main(
                    [
                        "--project-dir",
                        str(project),
                        "--backend",
                        "local",
                        "--arch",
                        "aarch64",
                        "--dry-run",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["builds"][0]["base_target"], "aarch64")
        self.assertEqual(payload["builds"][0]["target"], "aarch64")
        self.assertIsNone(payload["snapshot_key"])

    def test_registry_fallback_is_not_labeled_current_release(self):
        stderr = io.StringIO()
        with (
            mock.patch.dict(build_sailfishos.os.environ, {"SAILFISHOS_RELEASE": ""}),
            mock.patch.object(
                build_sailfishos,
                "latest_coderus_mirror_tag",
                return_value="5.2.0.15",
            ),
            redirect_stderr(stderr),
        ):
            release = build_sailfishos.resolve_release([Path("/nonexistent")], None)

        self.assertEqual(release, "5.2.0.15")
        self.assertIn("coderus/sailfishos-platform-sdk image tag", stderr.getvalue())
        self.assertIn("does not identify the current SailfishOS release", stderr.getvalue())

    def test_failure_classification(self):
        error = RuntimeError("error: Failed build dependencies: pkgconfig(foo) is needed by example")
        self.assertEqual(build_sailfishos.classify_failure(error), "missing-build-requires")
        self.assertEqual(
            build_sailfishos.classify_failure(RuntimeError("manifest unknown for Docker image")),
            "image",
        )

    def test_rpmlint_summary_uses_final_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "build.log"
            log.write_text(
                "2 packages and 1 specfiles checked; 3 errors, 4 warnings.\n",
                encoding="utf-8",
            )
            summary = build_sailfishos.rpmlint_summary(log)

        self.assertEqual(summary, {"errors": 3, "warnings": 4})

    def test_scoped_acl_preserves_executable_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(Path(temporary))
            with (
                mock.patch.object(build_sailfishos.shutil, "which", return_value="/usr/bin/setfacl"),
                mock.patch.object(build_sailfishos, "generated_candidate_paths", return_value=set()),
                mock.patch.object(build_sailfishos, "run") as run,
            ):
                build_sailfishos.ensure_container_write_access(project, "error")

        flattened = [item for call in run.call_args_list for item in call.args[0]]
        self.assertIn(f"u:{build_sailfishos.CONTAINER_UID}:rX", flattened)


if __name__ == "__main__":
    unittest.main()
