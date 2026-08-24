"""Unit tests for harness.validate (pure: synthetic sources only)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.validate import changed_lines, validate_component_rewrite

CLEAN_OLD = """package bot;

public class Economy {
    static int cheeseTarget = 10;

    static void collect(RobotController rc) throws GameActionException {
        if (rc.canPickupCheese()) {
            rc.pickupCheese();
        }
    }
}
"""


class TestChangedLines:
    def test_identical_is_zero(self):
        assert changed_lines(CLEAN_OLD, CLEAN_OLD) == 0

    def test_single_line_replacement_counts_two(self):
        new = CLEAN_OLD.replace("cheeseTarget = 10", "cheeseTarget = 25")
        assert changed_lines(CLEAN_OLD, new) == 2  # one removed + one added

    def test_pure_addition(self):
        assert changed_lines("a\nb\n", "a\nb\nc\nd\n") == 2

    def test_pure_removal(self):
        assert changed_lines("a\nb\nc\n", "a\n") == 2

    def test_empty_to_lines(self):
        assert changed_lines("", "x\ny\nz\n") == 3

    def test_symmetric(self):
        assert changed_lines("a\nb\n", "a\nc\n") == changed_lines("a\nc\n", "a\nb\n")


class TestValidateComponentRewrite:
    def test_clean_rewrite_ok(self):
        new = CLEAN_OLD.replace("cheeseTarget = 10", "cheeseTarget = 25")
        assert validate_component_rewrite(CLEAN_OLD, new) == []

    def test_identical_ok(self):
        assert validate_component_rewrite(CLEAN_OLD, CLEAN_OLD) == []

    def test_line_cap_violation(self):
        new = "\n".join(f"int v{i} = {i};" for i in range(300)) + "\n"
        violations = validate_component_rewrite(CLEAN_OLD, new, max_changed_lines=250)
        assert any("250" in v for v in violations)

    def test_line_cap_boundary_ok(self):
        old = ""
        new = "\n".join(f"int v{i} = {i};" for i in range(250)) + "\n"
        assert validate_component_rewrite(old, new, max_changed_lines=250) == []

    def test_custom_cap(self):
        violations = validate_component_rewrite("a\n", "b\nc\nd\n", max_changed_lines=2)
        assert len(violations) == 1

    @pytest.mark.parametrize(
        "needle",
        [
            "setIndicator",
            "IndicatorString",
            "java.io",
            "java.net",
            "java.nio.file",
            "Thread",
            "Runtime",
            "ProcessBuilder",
            "System.exit",
            "Class.forName",
            "reflect",
        ],
    )
    def test_forbidden_substrings_flagged(self, needle):
        new = CLEAN_OLD + f"\n// uses {needle} here\n"
        violations = validate_component_rewrite(CLEAN_OLD, new)
        assert any(needle in v for v in violations), violations

    def test_forbidden_api_examples(self):
        new = CLEAN_OLD.replace(
            "rc.pickupCheese();",
            'rc.setIndicatorString("note to reflector");',
        )
        violations = validate_component_rewrite(CLEAN_OLD, new)
        assert violations  # setIndicator and IndicatorString both hit
        assert any("setIndicator" in v for v in violations)

    def test_multiple_violations_all_reported(self):
        new = (
            "\n".join(f"int v{i} = {i};" for i in range(300))
            + "\nimport java.io.File;\nnew Thread(() -> {}).start();\n"
        )
        violations = validate_component_rewrite(CLEAN_OLD, new, max_changed_lines=250)
        assert len(violations) >= 3
