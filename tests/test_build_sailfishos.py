import importlib.util
import io
import json
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
                "--dry-run",
                "--json",
            ]
        )

        self.assertEqual(args.backend, "local")
        self.assertEqual(args.target, ["aarch64"])
        self.assertEqual(args.pull_policy, "missing")
        self.assertTrue(args.no_vcs_apply)
        self.assertTrue(args.allow_untrusted_rpms)

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
