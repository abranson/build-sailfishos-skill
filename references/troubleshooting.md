# Build failures and environment checks

## Troubleshooting

- If a local backend or exact target is requested and unavailable, report the
  registered base-target list from the helper error. Do not select an existing
  project snapshot or silently switch to an older mirror image.
- After a base update, expect the next build to reset its shared or isolated
  snapshot. Re-supply any snapshot repository, package, or local-RPM inputs;
  do not restore them in the base target.
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

Run `scripts/build_sailfishos.py --doctor` (or `sailfish_doctor`) to check local
tools, helper compatibility and installed SDK targets without contacting a device.
A configured helper that differs from the vendor is reported, not automatically
replaced. Review intentional overrides before syncing.

Dependency-provider results are saved under each build's `dependency_diagnostics`
in last-build JSON and appended to the full build log, including with `--quiet`.
Local diagnostics query the selected working snapshot, including its custom inputs.
A failed diagnostic lookup does not replace the original build failure.

Refresh stale metadata in the selected working snapshot with the asynchronous
`sailfish_sdk_refresh_metadata` tool. Without MCP:

```bash
python3 <skill-dir>/scripts/build_sailfishos.py --refresh-metadata \
  --backend local --target aarch64-project.default --quiet
```

This uses the same SDK wrapper as MCP. It does not create a missing target;
preflight/build the project first. Use `--force-refresh` only when necessary.

If SDK refresh reports `Invalid target specified`, verify that the user's
`~/.scratchbox2` registry is visible inside the SDK. Administrative wrappers mount
only that directory directly at `<sdkroot><home>/.scratchbox2` and use
`sdk-chroot -m root`; the default home bind would hide the nested mount. Do not
expose the whole host home as a workaround. `sb2 -m sdk-install -R` remains
required independently. Container user and primary-group mappings must both exist
before sdk-chroot imports them into the SDK.
