"""Execute generated SDK shell wrappers with local, non-privileged tool doubles."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_build_sailfishos import build_sailfishos as helper


class SdkWrapperTests(unittest.TestCase):
    def write_executable(self, path, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        path.chmod(0o755)
        return path

    def execute_wrapper(self, command, root, missing_group=False):
        environment = {**os.environ, 'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
                       'TEST_TRACE': str(root / 'trace.json')}
        environment.pop('SNAPSHOT_ROOT', None)
        for index, argument in enumerate(command):
            if argument == '-e':
                key, value = command[index + 1].split('=', 1)
                environment[key] = value
        # Supply an existing container user; neither /etc file is edited on the host.
        prefix = (
            'getent() { if [ "$1" = group ]; then '
            'printf "wrapper-test:x:%s:\\n" "$2"; return 0; fi; '
            '[ "$1" = passwd ] && [ "$2" = wrapper-test ]; };\n'
        )
        wrapper = command[-1]
        if missing_group:
            prefix = 'getent() { [ "$1" = passwd ] && [ "$2" = wrapper-test ]; };\n'
            group_file = root / 'group'
            group_file.write_text(f'other:x:55:\nlegacy:x:{os.getgid()}:\nwrapper-test:x:99:\n')
            wrapper = wrapper.replace('/etc/group', str(group_file))
        return subprocess.run(['bash', '-c', prefix + wrapper], env=environment,
                              cwd=root, capture_output=True, text=True, timeout=10)

    def fake_sdk(self, root):
        return self.write_executable(root / 'sdks' / 'sfossdk' / 'sdk-chroot', '''#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
assert args[:2] == ['-u', 'wrapper-test'], args
args = args[2:]
if args[:1] == ['-m']:
    assert args[:2] == ['-m', 'root'], args
    args = args[2:]
# Omit login-profile processing in the double so it keeps the test PATH.
args = ['-c' if value == '-lc' else value for value in args]
raise SystemExit(subprocess.call(args))
''')

    def test_shared_and_isolated_build_wrappers_reach_mb2(self):
        for target in ('aarch64', 'aarch64-example'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / 'project'
                (project / 'rpm').mkdir(parents=True)
                (project / 'rpm' / 'example.spec').write_text('Name: example\n')
                sdk = self.fake_sdk(root)
                self.write_executable(root / 'bin' / 'sdk-manage', '#!/bin/sh\nexit 0\n')
                self.write_executable(root / 'bin' / 'mb2', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['TEST_TRACE']).write_text(json.dumps(sys.argv[1:]))
Path('RPMS').mkdir(exist_ok=True)
Path('RPMS/example.rpm').write_text('test artifact')
''')
                with patch.object(helper, 'host_user', return_value='wrapper-test'), \
                     patch.object(helper, 'local_sdk_project_mount_root', return_value=root), \
                     patch.object(helper, 'run') as run:
                    helper.build_local_sdk_arch(project, sdk, 'live', 'aarch64', 'aarch64', target)
                command = run.call_args.args[0]
                result = self.execute_wrapper(command, root)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                expected = ['-t', target]
                if target != 'aarch64':
                    expected.append('--no-snapshot=force')
                expected.extend(['--no-vcs-apply', 'build', '--prepare'])
                self.assertEqual(json.loads((root / 'trace.json').read_text()), expected)
                self.assertTrue((helper.staging_rpms_dir(project) / 'example.rpm').exists())
                # Control: the historical unbound expansion must fail this execution test.
                broken = [*command[:-1], command[-1].replace(
                    'set -euo pipefail', 'set -euo pipefail\n: "$SNAPSHOT_ROOT"', 1)]
                result = self.execute_wrapper(broken, root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SNAPSHOT_ROOT', result.stderr)

    def test_refresh_mounts_only_registry_inside_sdk_and_keeps_sb2_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'
            registry = home / '.scratchbox2'
            registry.mkdir(parents=True)
            (registry / 'aarch64.default').mkdir()
            sdk = self.fake_sdk(root)
            with patch.object(helper, 'host_user', return_value='wrapper-test'), \
                 patch.object(helper.Path, 'home', return_value=home):
                command = helper.sdk_refresh_command(sdk, 'aarch64.default', force=True)
            mounts = [command[i + 1] for i, value in enumerate(command) if value == '-v']
            sdk_home = sdk.parent / home.relative_to('/')
            self.assertEqual(mounts, [f'{sdk.parent}:{sdk.parent}',
                                     f'{registry}:{sdk_home}/.scratchbox2'])
            self.assertNotIn(f'{home}:{home}', mounts)
            self.assertIn('-m root', command[-1])
            self.write_executable(root / 'bin' / 'sb2', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['TEST_TRACE']).write_text(json.dumps(sys.argv[1:]))
''')
            result = self.execute_wrapper(command, root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads((root / 'trace.json').read_text()),
                             ['-t', 'aarch64.default', '-m', 'sdk-install', '-R', 'zypper', 'ref', '-f'])
            result = self.execute_wrapper(command, root, missing_group=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual((root / 'group').read_text(),
                             f'other:x:55:\nwrapper-test:x:{os.getgid()}:\n')


if __name__ == '__main__':
    unittest.main()
