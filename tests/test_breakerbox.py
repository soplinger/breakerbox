import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from breakerbox import (  # noqa: E402
    PanelError,
    analyse,
    fit_circuit,
    imbalance_pct,
    main,
    parse_panel,
    report_to_dict,
    suggest_moves,
)


def test_parse_reads_service_spaces_and_defaults():
    panel = parse_panel("service 200 A\nKitchen | A | 20 | 14\n")
    assert panel.service_amps == 200
    assert panel.spaces == 42
    c = panel.circuits[0]
    assert (c.name, c.legs, c.breaker, c.load, c.continuous) == ("Kitchen", "A", 20, Decimal("14"), False)
    assert c.line == 2


def test_parse_ignores_comments_blank_lines_and_accepts_amp_suffix():
    text = "# house\n\nservice 200A\nspaces 30\n\nRange | ab | 50 A | 36 A | no\n"
    panel = parse_panel(text)
    assert panel.spaces == 30
    assert panel.circuits[0].legs == "AB"
    assert panel.circuits[0].load == Decimal("36")


def test_parse_collects_every_line_numbered_error():
    text = "service 200 A\nKitchen | X | twenty | 14 | maybe\nno pipes here\n"
    with pytest.raises(PanelError) as info:
        parse_panel(text)
    errors = "\n".join(info.value.errors)
    assert "line 2: legs must be A, B or AB" in errors
    assert "line 2: breaker must be a positive whole number" in errors
    assert "line 2: continuous must be yes or no" in errors
    assert "line 3: unknown directive" in errors


def test_parse_requires_service_line():
    with pytest.raises(PanelError) as info:
        parse_panel("spaces 10\nKitchen | A | 20 | 5\n")
    assert "no 'service <amps> A' line" in info.value.errors[0]


def test_imbalance_is_percent_of_heavier_leg():
    assert imbalance_pct(Decimal("0"), Decimal("0")) == 0
    assert imbalance_pct(Decimal("100"), Decimal("80")) == 20


def test_double_pole_counts_on_both_legs_and_two_spaces():
    panel = parse_panel("service 200 A\nRange | AB | 50 | 36\n")
    report = analyse(panel)
    assert report.legs == {"A": Decimal("36"), "B": Decimal("36")}
    assert report.spaces_used == 2
    assert report.passed()


def test_continuous_load_is_planned_at_125_percent():
    panel = parse_panel("service 200 A\nEV | AB | 40 | 33 | yes\n")
    report = analyse(panel)
    assert report.legs["A"] == Decimal("41.25")
    warnings = [f.message for f in report.warnings]
    assert any("needs 41.3 A" in m for m in warnings)


def test_overloaded_circuit_is_an_error_with_its_line():
    panel = parse_panel("service 200 A\nOven | A | 20 | 25\n")
    report = analyse(panel)
    assert not report.passed()
    assert report.errors[0].line == 2
    assert "exceeds the 20 A breaker" in report.errors[0].message


def test_nonstandard_breaker_is_a_warning():
    panel = parse_panel("service 200 A\nPump | A | 22 | 10\nHeater | B | 15 | 10\n")
    report = analyse(panel)
    assert report.passed()
    assert report.warnings[0].message == "breaker 22 A is not a standard size"
    assert not report.passed(strict=True)


def test_space_overflow_and_imbalance_limit_fail():
    panel = parse_panel("service 200 A\nspaces 2\nKitchen | A | 20 | 20\nHeater | B | 20 | 5\nLights | A | 15 | 2\n")
    report = analyse(panel, max_imbalance=Decimal("10"))
    messages = [f.message for f in report.errors]
    assert any("use 3 spaces but the panel has 2" in m for m in messages)
    assert any("legs are" in m and "limit is 10" in m for m in messages)
    assert not report.passed()


def test_suggest_moves_reduces_imbalance_and_never_moves_double_pole():
    text = (
        "service 200 A\n"
        "Kitchen | A | 20 | 14\n"
        "Living | A | 15 | 8\n"
        "Bedroom | B | 15 | 3\n"
        "Range | AB | 50 | 10\n"
    )
    panel = parse_panel(text)
    moves, totals = suggest_moves(panel)
    assert [m.name for m in moves] == ["Living"]
    assert moves[0].src == "A" and moves[0].dst == "B"
    assert moves[0].after < moves[0].before
    assert totals == {"A": Decimal("24"), "B": Decimal("21")}
    assert all(m.name != "Range" for m in moves)


def test_suggest_moves_respects_service_limit():
    panel = parse_panel("service 15 A\nKitchen | A | 20 | 14\nBedroom | B | 15 | 3\n")
    moves, totals = suggest_moves(panel)
    assert all(v <= 15 for v in totals.values())


def test_fit_picks_lighter_leg_and_auto_sizes_breaker():
    panel = parse_panel("service 200 A\nKitchen | A | 20 | 14\nBedroom | B | 15 | 3\n")
    plan = fit_circuit(panel, Decimal("5"))
    assert plan.leg == "B"
    assert plan.breaker == 15
    assert plan.spaces_free_after == 39


def test_fit_continuous_uses_125_percent_for_breaker():
    panel = parse_panel("service 200 A\nKitchen | A | 20 | 14\n")
    plan = fit_circuit(panel, Decimal("16"), continuous=True)
    assert plan.breaker == 20
    assert plan.leg == "B"


def test_fit_reports_problems_when_nothing_fits():
    panel = parse_panel("service 15 A\nKitchen | A | 20 | 14\nBedroom | B | 15 | 3\n")
    plan = fit_circuit(panel, Decimal("14"), double=True)
    assert plan.leg is None
    assert any("over the 15 A service" in p for p in plan.problems)


def test_fit_double_needs_two_free_spaces():
    panel = parse_panel("service 200 A\nspaces 3\nKitchen | A | 20 | 14\nBedroom | B | 15 | 3\n")
    plan = fit_circuit(panel, Decimal("10"), double=True)
    assert plan.leg is None
    assert any("needs 2 space(s), only 1 free" in p for p in plan.problems)


def test_report_dict_is_json_ready():
    panel = parse_panel("service 200 A\nKitchen | A | 20 | 14\nBedroom | B | 15 | 13\n")
    data = report_to_dict(analyse(panel), "x.panel", strict=False)
    text = json.dumps(data)
    assert json.loads(text)["passed"] is True
    assert data["legs"] == {"A": "14.0", "B": "13.0"}


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.panel"
    good.write_text("service 200 A\nKitchen | A | 20 | 10\nHeater | B | 20 | 10\n")
    bad = tmp_path / "bad.panel"
    bad.write_text("service 200 A\nOven | A | 20 | 25\n")
    broken = tmp_path / "broken.panel"
    broken.write_text("service 200 A\nOven | Z | 20 | 25\n")

    assert main(["check", str(good)]) == 0
    assert "Result: PASS" in capsys.readouterr().out
    assert main(["check", str(bad)]) == 1
    assert "Result: FAIL" in capsys.readouterr().out
    assert main(["check", str(broken)]) == 2
    assert "legs must be A, B or AB" in capsys.readouterr().err


def test_cli_json_output(tmp_path, capsys):
    path = tmp_path / "p.panel"
    path.write_text("service 200 A\nKitchen | A | 20 | 10\nHeater | B | 20 | 10\n")
    assert main(["check", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
