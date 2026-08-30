# Security policy

## Supported versions

The latest release on PyPI and crates.io. Earlier versions stay published
permanently, because results recorded against them replay under those exact
versions, and they receive no fixes.

| version | supported |
|---|---|
| 0.6.x | yes |
| 0.5.x and earlier | no, and published forever |

## Reporting a vulnerability

Report privately through GitHub's
[security advisory form](https://github.com/simoncoombes/tradefloor/security/advisories/new),
which opens a channel visible only to the maintainer. Please do not open a
public issue for a vulnerability.

Expect an acknowledgement within seven days. If a report is accepted, the
advisory carries the fix and the release that contains it.

## In scope

This is a simulator. It runs untrusted-looking inputs on your own machine and
talks to no network by default, so its risk surface is narrower than a
service, and these are the reports worth sending.

- **Deserialisation.** `Checkpoint.from_json`, `RunManifest` and the scenario
  loader all read files a user may have received from somebody else.
  `tradefloor.yaml_subset` exists precisely because a general YAML parser can
  construct arbitrary objects; a way to make it do so is a vulnerability.
- **The MCP server.** `tradefloor-mcp` takes input from a model, which is to
  say from anywhere. An input that reaches the filesystem or the process
  beyond the documented tools is a vulnerability.
- **A dependency** this project pins that carries a known advisory.

## Out of scope

- **A market that behaves unrealistically.** That is a modelling gap, and the
  ones this project knows about are published in `tradefloor.envelope`. Open
  an issue.
- **A result you cannot reproduce.** That is a determinism defect and is
  treated as serious, but in public: the whole gate exists to catch it, and
  hiding one helps nobody. Open an issue with the seed, the preset and the
  platform.
- **Anything about trading real money.** This library simulates a market and
  makes no claim about any real one.
