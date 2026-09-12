# Build SailfishOS Skill

This repository contains a Codex skill for building SailfishOS RPMs with Docker and `mb2`.

The skill includes:

- [SKILL.md](./SKILL.md): usage instructions for Codex
- [scripts/build_sailfishos.py](./scripts/build_sailfishos.py): the helper script that performs the build workflow
- [agents/openai.yaml](./agents/openai.yaml): skill metadata for invocation

## What It Does

The helper script can:

- resolve an explicit release or shorthand to a matching tag in the third-party
  `coderus/sailfishos-platform-sdk` mirror, with optional newest-tag selection
- discover a SailfishOS build root one level below a repo root
- clean stale in-place build artifacts when switching architectures
- perform a Docker shadow build with `mb2`
- let `mb2` share its standard `.default` snapshot per installed SDK base for
  ordinary builds, using isolated original targets only for custom
  repositories, packages, local RPMs, or an explicit isolation key
- group custom browser worktrees and repositories by ESR generation while
  allowing an explicit stable snapshot key for other logical groupings
- always pass original targets to `mb2`, preventing nested
  `.default.default` snapshot chains
- restore declared snapshot repositories, packages, local RPMs, and
  BuildRequires after the base target changes and invalidates a snapshot
- archive RPMs under `RPMS/<release>/<arch>/<release|debug>/`
- keep direct builds quiet while retaining the complete project build log
- generate debug packages when `--debug` is used
- save build logs and machine-readable build metadata under `.mb2/`

## Installation

Codex installs skills under `$CODEX_HOME/skills`, which defaults to `~/.codex/skills`.

Manual install:

1. Copy or clone this repository to `~/.codex/skills/build-sailfishos`
2. Restart Codex

## Usage

Invoke the skill in Codex with:

```text
$build-sailfishos
```

## Support

Support is handled through the repository issue tracker.

Maintainer:

- Author: Andrew Branson

## License

This project is licensed under the 0BSD license. See [LICENSE](./LICENSE).
