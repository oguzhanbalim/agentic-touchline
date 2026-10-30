# Data and third-party notices

## StatsBomb Open Data

This project reads [StatsBomb Open Data](https://github.com/statsbomb/open-data).
The data is the property of StatsBomb and is governed by the
[StatsBomb Public Data User Agreement](https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf),
not by this repository's MIT license. Anyone running this code enters that
agreement themselves. StatsBomb also asks users to register their interest at
<https://statsbomb.com/resource-centre>.

How the repository handles the terms:

| Clause | What it asks | What this repository does |
| --- | --- | --- |
| 1.2.1 | Do not redistribute or reproduce the data | No StatsBomb data file is committed. `touchline ingest` downloads to the gitignored `data/` directory. Test fixtures are synthetic. `tests/test_repo_guard.py` fails if a data file becomes tracked. |
| 1.2.2 | No commercial exploitation of the data or derived analysis | Research and demonstration only. Do not use this code with this data commercially. |
| 1.2.4 | Nothing defamatory or damaging to any individual or organisation | The agent prompts prohibit judgements of character and require describing actions in a match. A human approves every report before release. |
| 1.4 | Accredit published analysis with the StatsBomb brand logo | The logo appears in the README and on every generated chart. |
| Preamble | Analysis is not necessarily StatsBomb's opinion | Stated in the README. |

The agents send derived figures from local tools (aggregates and a per-shot
summary) to the Anthropic Messages API. They do not send raw event records,
coordinates or freeze frames. Whether that counts as
"providing the data" under clause 1.2.1 is a question for whoever runs the live
agent; the live agent has not been run from this repository.

## StatsBomb logo

`src/touchline/assets/statsbomb_logo.png` is a downscaled copy of
`img/SB - Icon Lockup - Colour positive.png` from the StatsBomb open-data
repository. It is a StatsBomb trademark, is included only to meet clause 1.4,
and is not covered by the MIT license.

## Independence

Not endorsed by any club, player or federation.
Not affiliated with Anthropic or StatsBomb.
