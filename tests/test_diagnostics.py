import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
from test_build_sailfishos import build_sailfishos as helper


class DiagnosticTests(unittest.TestCase):
    def test_quiet_provider_diagnostics_are_persisted_for_both_backends(self):
        for local in (False, True):
            with self.subTest(local=local), tempfile.TemporaryDirectory() as d:
                project=Path(d);log=helper.build_log_path(project);log.parent.mkdir()
                log.write_text('error: Failed build dependencies:\n    pkgconfig(example) is needed by sample\nend\n')
                reply=subprocess.CompletedProcess([],0,'S | Name | Type\n  | example-devel | package\n','')
                with patch.object(helper,'_QUIET_OUTPUT',True),patch.object(helper.subprocess,'run',return_value=reply) as run:
                    diagnostics=helper.diagnose_missing_dependencies(project,'5.3.0','aarch64',
                        local_sdk=Path('/srv/mer/sdks/sfossdk/sdk-chroot') if local else None,
                        target='aarch64-project.default' if local else None)
                self.assertEqual(diagnostics[0]['providers'],['example-devel'])
                self.assertIn('Dependency diagnostics:',log.read_text())
                command=run.call_args.args[0]
                self.assertIn('aarch64-project.default' if local else 'SailfishOS-5.3.0-aarch64',command[-1])
                self.assertEqual(run.call_args.kwargs['timeout'],15)
                helper.write_build_metadata(project,context=helper.BuildContext('local' if local else 'docker','5.3.0',None,None,None),
                    builds=[{'arch':'aarch64','dependency_diagnostics':diagnostics}],debug_build=False,
                    artifacts_dir=project/'RPMS',status='failed',started_at=helper.datetime.now(helper.timezone.utc))
                metadata=json.loads(helper.build_metadata_path(project).read_text())
                self.assertEqual(metadata['builds'][0]['dependency_diagnostics'],diagnostics)

    def test_provider_timeout_keeps_original_failure_available(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);log=helper.build_log_path(root);log.parent.mkdir()
            log.write_text('error: Failed build dependencies:\n    example is needed by sample\nend\n')
            with patch.object(helper.subprocess,'run',side_effect=subprocess.TimeoutExpired('docker',15)):
                result=helper.diagnose_missing_dependencies(root,'5.3.0','aarch64')
            self.assertIn('error',result[0]);self.assertIn('Failed build dependencies',log.read_text())

    def test_doctor_works_without_docker_and_does_not_build(self):
        with patch.object(helper.shutil,'which',return_value=None),patch.object(helper,'run',side_effect=AssertionError('build')), \
             patch.object(helper,'list_local_sdk_targets',return_value=[]),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(helper.main(['--doctor']),0)
        self.assertFalse(json.loads(output.getvalue())['tools']['docker'])

    def test_refresh_cli_selects_working_target_and_force_flag(self):
        with patch.object(helper,'run') as run:
            self.assertEqual(helper.main(['--refresh-metadata','--target','aarch64-project.default',
                                          '--force-refresh','--quiet']),0)
        command=run.call_args.args[0]
        self.assertIn(f'install -d -m 0755 -o {helper.os.getuid()} -g {helper.os.getgid()} {Path.home().resolve()}',command[-1])
        home=str(Path.home().resolve())
        self.assertIn(f'{home}/.scratchbox2:{home}/.scratchbox2',command)
        self.assertIn('sb2 -t aarch64-project.default',command[-1])
        self.assertIn('zypper ref -f',command[-1]);self.assertNotIn('.default.default',command[-1])

    def test_refresh_rejects_ambiguous_or_docker_target(self):
        for arguments in (['--refresh-metadata'], ['--refresh-metadata','--target','aarch64','--target','armv7hl'],
                          ['--refresh-metadata','--target','aarch64','--dry-run']):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit),redirect_stderr(io.StringIO()):
                helper.main(arguments)


if __name__=='__main__':unittest.main()
