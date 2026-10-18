# Breakerbox

**Plan a household electrical panel from one plain-text file.** Breakerbox balances the two legs of a split-phase panel, checks breaker and service rules, suggests the moves that even things out, and tells you where a new circuit fits, all offline in one stdlib Python file.

> Planning aid only. Breakerbox applies a few simple rules (125 % for continuous loads, standard breaker sizes, leg balance). It is not a load calculation under your local electrical code and does not replace a licensed electrician's inspection.

## Features

- **Plain-text panel files** that are easy to edit, diff and keep under version control.
- **Line-numbered parse errors.** Every bad field is reported at once, with its line number.
- **Per-leg load totals** for legs A and B, with a bar chart against the service rating.
- **Double-pole (240 V) circuits** counted on both legs and charged two spaces.
- **Continuous-load planning** at 125 % of the measured load, checked against the breaker.
- **Breaker checks** for overloads, undersized continuous circuits and non-standard sizes.
- **Service checks** that flag any leg whose design load exceeds the service rating.
- **Space accounting** that catches over-full panels and shows how many spaces are free.
- **Leg imbalance** as a percentage of the heavier leg, with a configurable limit.
- **Balance planner** that proposes single-circuit moves, each with its before and after imbalance. Double-pole circuits are never moved.
- **New-circuit fit test** that picks the best leg, sizes the breaker if you don't give one, and explains why a circuit does not fit.
- **JSON output** for scripts and dashboards.
- **CI-ready exit codes.** `check` returns 1 on failure, and `--strict` also fails on warnings.

## Quick start

Create a file called `house.panel`:

```text
service 200 A
spaces 42

# name                        | legs | breaker A | load A | [continuous?]
Kitchen counter receptacles   | A    | 20 | 14
Living room receptacles       | A    | 15 | 8
Bedroom 1 lighting            | B    | 15 | 3
Bathroom receptacles          | B    | 20 | 5
Range                         | AB   | 50 | 36
Heat pump                     | AB   | 40 | 25 | yes
```

Then ask Breakerbox four questions:

```sh
# Is the panel OK? Shows leg bars, imbalance and every finding.
python3 breakerbox.py check house.panel

# What single-circuit moves would even out the legs?
python3 breakerbox.py balance house.panel

# Where does a new 16 A continuous circuit fit, and what breaker does it need?
python3 breakerbox.py fit house.panel --load 16 --continuous

# Same check as JSON, for a script or dashboard.
python3 breakerbox.py check house.panel --json
```

A complete example with ten circuits is in `examples/house.panel`. Running `balance` on it suggests moving the 14 A kitchen circuit to leg B. That brings both legs to 157.25 A design load, which is 0.0 % imbalance.

## How to run

Requires Python 3.9 or newer. There are no dependencies to install and nothing to start.

```sh
python3 breakerbox.py --help
python3 breakerbox.py check examples/house.panel
```

### Panel file format

| Line | Meaning |
| --- | --- |
| `service 200 A` | Service rating per leg. Required, once. |
| `spaces 42` | Breaker spaces in the panel. Optional, defaults to 42. |
| `name \| legs \| breaker A \| load A \| [yes/no]` | One circuit. `legs` is `A`, `B` or `AB`. The last field is optional and defaults to `no`. |
| `# ...` | Comment. Blank lines are ignored. |

Exit codes: `check` returns 0 on pass, 1 on fail and 2 on a bad panel file. `fit` returns 0 when the circuit fits and 1 when it does not.

## How to test

The tests use pytest and cover parsing, leg and continuous-load maths, findings, balancing, fitting and the CLI.

```sh
python3 -m pip install pytest
python3 -m pytest
```

## Project layout

```text
breakerbox.py          the whole tool: parser, analysis, planners and CLI
examples/house.panel   a ten-circuit example household panel
tests/test_breakerbox.py
```
