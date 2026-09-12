# Security policy

## Supported versions

The latest release on PyPI and crates.io. Earlier versions stay published
permanently, because results recorded against them replay under those exact
versions, and they receive no fixes.

| version | supported |
|---|---|
| 0.8.x | yes |
| 0.7.x and earlier | no, and published forever |

## Reporting a vulnerability

Report privately through GitHub's
[security advisory form](https://github.com/simoncoombes/tradefloor/security/advisories/new),
which opens a channel visible only to the maintainer. Please do not open a
public issue for a vulnerability.

Expect an acknowledgment within seven days. If a report is accepted, the
advisory carries the fix and the release that contains it.

## In scope

This is a simulator. It runs untrusted-looking inputs on your own machine and
talks to no network by default, so its risk surface is narrower than a
service.

- `Checkpoint.from_json`, `RunManifest` and the scenario loader all read
  files a user may have received from somebody else. `tradefloor.yaml_subset`
  exists because a general YAML parser can construct arbitrary objects, and a
  way to make it do so is a vulnerability.
- `tradefloor-mcp` takes input from a model, which is to say from anywhere.
  An input that reaches the filesystem or the process beyond the documented
  tools is a vulnerability.
- A known advisory in a dependency this project pins is worth a report.

## Out of scope

- A market that behaves unrealistically is a modeling gap, and the ones this
  project knows about are published in `tradefloor.envelope`. Open an issue.
- A result you cannot reproduce is a determinism defect, and it is treated as
  serious and handled in public, because the determinism gate exists to catch
  exactly that. Open an issue with the seed, the preset and the platform.
- A report about trading real money has nothing here to fix. This library
  simulates a market and makes no claim about any real one.
