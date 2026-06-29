# Build SailfishOS Skill

This repository contains a Codex skill for building SailfishOS RPMs with Docker and `mb2`.

The skill includes:

- [SKILL.md](./SKILL.md): usage instructions for Codex
- [scripts/build_sailfishos.py](./scripts/build_sailfishos.py): the helper script that performs the build workflow
- [agents/openai.yaml](./agents/openai.yaml): skill metadata for invocation

## What It Does

The helper script can:

- resolve a SailfishOS SDK release, including shorthand versions and the latest available release
- discover a SailfishOS build root one level below a repo root
- clean stale in-place build artifacts when switching architectures
- perform a Docker shadow build with `mb2`
- document how to use an installed `/srv/mer` devel SDK target, including the privileged Docker wrapper used when direct host `sudo` is unavailable
- archive RPMs under `RPMS/<release>/<arch>/<release|debug>/`
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
