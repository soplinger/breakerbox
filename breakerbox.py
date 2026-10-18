#!/usr/bin/env python3
"""Breakerbox: plan a household electrical panel from one plain-text file.

Offline, standard library only. Breakerbox balances the two legs of a
split-phase panel, checks simple breaker and service rules, suggests
single-circuit moves that even out the legs, and tests where a new circuit
would fit. It is a planning aid, not a load calculation under your local
electrical code, and it does not replace a licensed electrician's inspection.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable

CONTINUOUS_FACTOR = Decimal("1.25")
DEFAULT_SPACES = 42
DEFAULT_MAX_IMBALANCE = Decimal("10")
BAR_WIDTH = 40
LEGS = ("A", "B", "AB")
STANDARD_BREAKERS = (
    15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110,
    125, 150, 175, 200, 225, 250, 300, 350, 400,
)
USAGE_HELP = """\
panel file format (one directive or circuit per line, # starts a comment):

  service 200 A
  spaces 42
  name | legs | breaker A | load A | [yes/no continuous]

  legs is A, B or AB (AB = double-pole 240 V, counted on both legs and
  using two spaces). Continuous loads run 3 hours or more and are planned
  at 125 % of their measured load.
"""


class PanelError(Exception):
    """Raised when a panel file has one or more line-numbered problems."""

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Circuit:
    line: int
    name: str
    legs: str
    breaker: int
    load: Decimal
    continuous: bool

    @property
    def spaces(self) -> int:
        return 2 if self.legs == "AB" else 1

    @property
    def design(self) -> Decimal:
        """Planning load: continuous loads count at 125 %."""
        return self.load * CONTINUOUS_FACTOR if self.continuous else self.load


@dataclass
class Panel:
    service_amps: int
    spaces: int = DEFAULT_SPACES
    circuits: list[Circuit] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    line: int | None
    message: str


@dataclass
class Report:
    panel: Panel
    legs: dict[str, Decimal]
    spaces_used: int
    imbalance: Decimal
    max_imbalance: Decimal
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    def passed(self, strict: bool = False) -> bool:
        if self.errors:
            return False
        return not (strict and self.warnings)


@dataclass(frozen=True)
class Move:
    line: int
    name: str
    src: str
    dst: str
    before: Decimal
    after: Decimal


@dataclass(frozen=True)
class FitPlan:
    leg: str | None
    imbalance_after: Decimal | None
    breaker: int | None
    spaces_free_after: int | None
    problems: tuple[str, ...]


def _amps(token: str) -> str:
    """Strip an optional trailing unit 'A' from a number token."""
    token = token.strip()
    if token[-1:] in ("A", "a"):
        token = token[:-1].strip()
    return token


def _one_dp(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _parse_circuit(lineno: int, text: str, errors: list[str]) -> Circuit | None:
    parts = [p.strip() for p in text.split("|")]
    if len(parts) not in (4, 5):
        errors.append(
            f"line {lineno}: expected 'name | legs | breaker A | load A | [yes/no]', "
            f"got {len(parts)} field(s)"
        )
        return None
    name, legs_text, breaker_text, load_text = parts[:4]
    cont_text = parts[4].lower() if len(parts) == 5 else "no"
    ok = True
    if not name:
        errors.append(f"line {lineno}: circuit name is empty")
        ok = False
    legs = legs_text.upper()
    if legs not in LEGS:
        errors.append(f"line {lineno}: legs must be A, B or AB, got {legs_text!r}")
        ok = False
    try:
        breaker = int(_amps(breaker_text))
        if breaker <= 0:
            raise ValueError
    except ValueError:
        errors.append(f"line {lineno}: breaker must be a positive whole number of amps, got {breaker_text!r}")
        ok = False
    try:
        load = Decimal(_amps(load_text))
        if not load.is_finite() or load < 0:
            raise InvalidOperation
    except InvalidOperation:
        errors.append(f"line {lineno}: load must be a non-negative number of amps, got {load_text!r}")
        ok = False
    if cont_text not in ("yes", "no"):
        errors.append(f"line {lineno}: continuous must be yes or no, got {parts[4]!r}")
        ok = False
    if not ok:
        return None
    return Circuit(lineno, name, legs, breaker, load, cont_text == "yes")


def parse_panel(text: str) -> Panel:
    """Parse a panel file, collecting every line-numbered error before failing."""
    errors: list[str] = []
    service: int | None = None
    spaces = DEFAULT_SPACES
    circuits: list[Circuit] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            circuit = _parse_circuit(lineno, line, errors)
            if circuit is not None:
                circuits.append(circuit)
            continue
        head, _, rest = line.partition(" ")
        head = head.lower()
        if head == "service":
            if service is not None:
                errors.append(f"line {lineno}: duplicate service line")
                continue
            try:
                service = int(_amps(rest))
                if service <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"line {lineno}: service must be a positive number of amps, got {rest.strip()!r}")
        elif head == "spaces":
            try:
                spaces = int(rest.strip())
                if spaces <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"line {lineno}: spaces must be a positive whole number, got {rest.strip()!r}")
        else:
            errors.append(f"line {lineno}: unknown directive {head!r} (use 'service', 'spaces' or a circuit row)")
    if service is None:
        errors.append("no 'service <amps> A' line found")
    if errors:
        raise PanelError(errors)
    return Panel(service_amps=service, spaces=spaces, circuits=circuits)


def _totals(pairs: Iterable[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Sum design loads per leg. A 'AB' entry counts on both legs."""
    totals = {"A": Decimal(0), "B": Decimal(0)}
    for legs, design in pairs:
        for leg in legs:
            totals[leg] += design
    return totals


def imbalance_pct(a: Decimal, b: Decimal) -> Decimal:
    """Gap between the legs as a percentage of the heavier leg."""
    hi = max(a, b)
    if hi == 0:
        return Decimal(0)
    return (hi - min(a, b)) * 100 / hi


def analyse(panel: Panel, max_imbalance: Decimal = DEFAULT_MAX_IMBALANCE) -> Report:
    findings: list[Finding] = []
    for c in panel.circuits:
        if c.breaker not in STANDARD_BREAKERS:
            findings.append(Finding("warning", c.line, f"breaker {c.breaker} A is not a standard size"))
        if c.load > c.breaker:
            findings.append(Finding("error", c.line, f"load {_one_dp(c.load)} A exceeds the {c.breaker} A breaker"))
        elif c.continuous and c.design > c.breaker:
            findings.append(Finding(
                "warning", c.line,
                f"continuous load needs {_one_dp(c.design)} A at 125 %, breaker is {c.breaker} A",
            ))
    used = sum(c.spaces for c in panel.circuits)
    if used > panel.spaces:
        findings.append(Finding("error", None, f"circuits use {used} spaces but the panel has {panel.spaces}"))
    legs = _totals((c.legs, c.design) for c in panel.circuits)
    for leg in ("A", "B"):
        if legs[leg] > panel.service_amps:
            findings.append(Finding(
                "error", None,
                f"leg {leg} design load {_one_dp(legs[leg])} A exceeds the {panel.service_amps} A service",
            ))
    imb = imbalance_pct(legs["A"], legs["B"])
    if imb > max_imbalance:
        findings.append(Finding(
            "error", None,
            f"legs are {_one_dp(imb)}% apart, limit is {_one_dp(max_imbalance)}%",
        ))
    return Report(panel, legs, used, imb, max_imbalance, findings)


def suggest_moves(panel: Panel, max_moves: int = 10) -> tuple[list[Move], dict[str, Decimal]]:
    """Greedily move single-pole circuits between legs while balance improves."""
    circuits = panel.circuits
    assign = [c.legs for c in circuits]
    totals = _totals((a, c.design) for a, c in zip(assign, circuits))
    moves: list[Move] = []
    for _ in range(max_moves):
        current = imbalance_pct(totals["A"], totals["B"])
        best = None
        for i, c in enumerate(circuits):
            if assign[i] not in ("A", "B"):
                continue
            dst = "B" if assign[i] == "A" else "A"
            trial = list(assign)
            trial[i] = dst
            t = _totals((a, cc.design) for a, cc in zip(trial, circuits))
            if max(t.values()) > panel.service_amps:
                continue
            score = imbalance_pct(t["A"], t["B"])
            if score < current and (best is None or score < best[0]):
                best = (score, i, dst, t)
        if best is None:
            break
        score, i, dst, t = best
        moves.append(Move(circuits[i].line, circuits[i].name, assign[i], dst, current, score))
        assign[i] = dst
        totals = t
    return moves, totals


def fit_circuit(
    panel: Panel,
    load: Decimal,
    breaker: int | None = None,
    continuous: bool = False,
    double: bool = False,
) -> FitPlan:
    """Find the leg where a new circuit fits best, or say why it does not."""
    design = load * CONTINUOUS_FACTOR if continuous else load
    if breaker is None:
        breaker = next((b for b in STANDARD_BREAKERS if b >= design), None)
        if breaker is None:
            return FitPlan(None, None, None, None, (f"no standard breaker covers {_one_dp(design)} A",))
    spaces_needed = 2 if double else 1
    used = sum(c.spaces for c in panel.circuits)
    free = panel.spaces - used
    shared: list[str] = []
    if breaker not in STANDARD_BREAKERS:
        shared.append(f"{breaker} A is not a standard breaker size")
    if load > breaker:
        shared.append(f"load {_one_dp(load)} A exceeds the {breaker} A breaker")
    elif design > breaker:
        shared.append(f"continuous load needs {_one_dp(design)} A at 125 %, breaker is {breaker} A")
    if spaces_needed > free:
        shared.append(f"needs {spaces_needed} space(s), only {free} free")
    base = [(c.legs, c.design) for c in panel.circuits]
    candidates = ["AB"] if double else ["A", "B"]
    best: tuple[str, Decimal] | None = None
    problems: list[str] = list(shared)
    for leg in candidates:
        t = _totals(base + [(leg, design)])
        local = list(shared)
        for name in ("A", "B"):
            if t[name] > panel.service_amps:
                local.append(f"leg {name} would carry {_one_dp(t[name])} A, over the {panel.service_amps} A service")
        problems.extend(p for p in local if p not in problems)
        if local:
            continue
        imb = imbalance_pct(t["A"], t["B"])
        if best is None or imb < best[1]:
            best = (leg, imb)
    if best is None:
        return FitPlan(None, None, breaker, None, tuple(problems))
    return FitPlan(best[0], best[1], breaker, free - spaces_needed, ())


def _bar(amps: Decimal, scale: int) -> str:
    filled = min(BAR_WIDTH, int(amps * BAR_WIDTH / scale + Decimal("0.5")))
    return "#" * filled + "." * (BAR_WIDTH - filled)


def render_check(report: Report, name: str, strict: bool) -> str:
    p = report.panel
    lines = [
        f"Breakerbox check: {name}",
        f"Service {p.service_amps} A per leg | spaces {report.spaces_used}/{p.spaces} used",
        "",
    ]
    for leg in ("A", "B"):
        amps = report.legs[leg]
        pct = amps * 100 / p.service_amps
        lines.append(f"Leg {leg} {_bar(amps, p.service_amps)} {_one_dp(amps):>7} A  ({_one_dp(pct)}% of service)")
    lines.append(f"Imbalance: {_one_dp(report.imbalance)}% (limit {_one_dp(report.max_imbalance)}%)")
    lines.append("")
    if not report.findings:
        lines.append("No findings.")
    for f in report.findings:
        where = f"line {f.line}" if f.line is not None else "panel"
        lines.append(f"  {f.level:<8}{where}: {f.message}")
    lines.append("")
    lines.append("Result: " + ("PASS" if report.passed(strict) else "FAIL"))
    return "\n".join(lines)


def render_balance(panel: Panel, moves: list[Move], totals: dict[str, Decimal], name: str) -> str:
    before = _totals((c.legs, c.design) for c in panel.circuits)
    start = imbalance_pct(before["A"], before["B"])
    lines = [f"Balance plan: {name}", f"Starting imbalance: {_one_dp(start)}%"]
    if not moves:
        lines.append("No single-circuit move improves the balance.")
    for n, m in enumerate(moves, start=1):
        lines.append(
            f"  {n}. line {m.line} '{m.name}': move {m.src} -> {m.dst}, "
            f"imbalance {_one_dp(m.before)}% -> {_one_dp(m.after)}%"
        )
    final = imbalance_pct(totals["A"], totals["B"])
    lines.append(
        f"Final legs: A {_one_dp(totals['A'])} A, B {_one_dp(totals['B'])} A "
        f"(imbalance {_one_dp(final)}%)"
    )
    lines.append("Double-pole (AB) circuits are never moved.")
    return "\n".join(lines)


def render_fit(plan: FitPlan, load: Decimal, continuous: bool) -> str:
    kind = "continuous" if continuous else "non-continuous"
    head = f"Fit {_one_dp(load)} A {kind} load"
    if plan.leg is None:
        lines = [head, "Result: does not fit"]
        lines += [f"  - {p}" for p in plan.problems]
        return "\n".join(lines)
    return "\n".join([
        head,
        f"Breaker: {plan.breaker} A",
        f"Result: fits on leg {plan.leg}, imbalance after {_one_dp(plan.imbalance_after)}%",
        f"Spaces left after: {plan.spaces_free_after}",
    ])


def report_to_dict(report: Report, name: str, strict: bool) -> dict:
    p = report.panel
    return {
        "panel": name,
        "service_amps": p.service_amps,
        "spaces_total": p.spaces,
        "spaces_used": report.spaces_used,
        "legs": {k: _one_dp(v) for k, v in report.legs.items()},
        "imbalance_pct": _one_dp(report.imbalance),
        "max_imbalance_pct": _one_dp(report.max_imbalance),
        "findings": [{"level": f.level, "line": f.line, "message": f.message} for f in report.findings],
        "passed": report.passed(strict),
    }


def _load_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _decimal_arg(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from exc
    if not value.is_finite() or value < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative number: {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="breakerbox",
        description="Plan a household panel's legs, breakers and free space from a plain-text file.",
        epilog=USAGE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="report leg loads, breaker rules and space use")
    check.add_argument("panel")
    check.add_argument("--json", action="store_true", help="print JSON instead of text")
    check.add_argument("--max-imbalance", type=_decimal_arg, default=DEFAULT_MAX_IMBALANCE,
                       help="largest allowed leg gap in percent (default 10)")
    check.add_argument("--strict", action="store_true", help="treat warnings as failures")

    balance = sub.add_parser("balance", help="suggest single-circuit moves that even out the legs")
    balance.add_argument("panel")
    balance.add_argument("--max-moves", type=int, default=10)

    fit = sub.add_parser("fit", help="test where a new circuit would fit")
    fit.add_argument("panel")
    fit.add_argument("--load", type=_decimal_arg, required=True, help="new circuit load in amps")
    fit.add_argument("--breaker", type=int, help="breaker size; default is the smallest standard size that covers the load")
    fit.add_argument("--continuous", action="store_true", help="load runs 3 hours or more")
    fit.add_argument("--double", action="store_true", help="240 V double-pole circuit (AB, two spaces)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        panel = parse_panel(_load_text(args.panel))
    except PanelError as exc:
        for msg in exc.errors:
            print(f"{args.panel}: {msg}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"breakerbox: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        strict = args.strict
        report = analyse(panel, args.max_imbalance)
        if args.json:
            print(json.dumps(report_to_dict(report, args.panel, strict), indent=2))
        else:
            print(render_check(report, args.panel, strict))
        return 0 if report.passed(strict) else 1

    if args.command == "balance":
        moves, totals = suggest_moves(panel, args.max_moves)
        print(render_balance(panel, moves, totals, args.panel))
        return 0

    plan = fit_circuit(panel, args.load, args.breaker, args.continuous, args.double)
    print(render_fit(plan, args.load, args.continuous))
    return 0 if plan.leg is not None else 1


if __name__ == "__main__":
    sys.exit(main())
