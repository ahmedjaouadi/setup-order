from __future__ import annotations

import unittest

from app.opportunity_scanner.rule_interpreter import evaluate_rule, validate_rule_structure


class OperatorTruthTableTests(unittest.TestCase):
    def test_gte(self) -> None:
        rule = {"field": "gap_pct", "op": ">=", "value": 3}
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3}))
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3.1}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 2.9}))

    def test_gt(self) -> None:
        rule = {"field": "gap_pct", "op": ">", "value": 3}
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 3}))
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3.1}))

    def test_lte(self) -> None:
        rule = {"field": "breakout_proximity", "op": "<=", "value": 1.5}
        self.assertTrue(evaluate_rule(rule, {"breakout_proximity": 1.5}))
        self.assertFalse(evaluate_rule(rule, {"breakout_proximity": 1.51}))

    def test_lt(self) -> None:
        rule = {"field": "breakout_proximity", "op": "<", "value": 1.5}
        self.assertFalse(evaluate_rule(rule, {"breakout_proximity": 1.5}))
        self.assertTrue(evaluate_rule(rule, {"breakout_proximity": 1.49}))

    def test_eq_numeric(self) -> None:
        rule = {"field": "gap_pct", "op": "==", "value": 3}
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 3.01}))

    def test_eq_boolean(self) -> None:
        rule = {"field": "new_intraday_high", "op": "==", "value": True}
        self.assertTrue(evaluate_rule(rule, {"new_intraday_high": True}))
        self.assertFalse(evaluate_rule(rule, {"new_intraday_high": False}))

    def test_between(self) -> None:
        rule = {"field": "gap_pct", "op": "between", "value": [2, 4]}
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 2}))
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 4}))
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 1.9}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 4.1}))

    def test_between_inverted_bounds_never_matches(self) -> None:
        rule = {"field": "gap_pct", "op": "between", "value": [4, 2]}
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 3}))

    def test_between_malformed_value_never_matches(self) -> None:
        rule = {"field": "gap_pct", "op": "between", "value": [2]}
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 2}))
        rule_not_a_list = {"field": "gap_pct", "op": "between", "value": "2,4"}
        self.assertFalse(evaluate_rule(rule_not_a_list, {"gap_pct": 2}))


class CombinatorTests(unittest.TestCase):
    def test_all_requires_every_condition(self) -> None:
        rule = {
            "all": [
                {"field": "gap_pct", "op": ">=", "value": 3},
                {"field": "perf_stock_1d", "op": ">", "value": 0},
            ]
        }
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3, "perf_stock_1d": 0.1}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 3, "perf_stock_1d": 0}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 2, "perf_stock_1d": 0.1}))

    def test_any_requires_one_condition(self) -> None:
        rule = {
            "any": [
                {"field": "rs_spy", "op": ">=", "value": 3},
                {"field": "rs_sector", "op": ">=", "value": 2},
            ]
        }
        self.assertTrue(evaluate_rule(rule, {"rs_spy": 3}))
        self.assertTrue(evaluate_rule(rule, {"rs_sector": 2}))
        self.assertFalse(evaluate_rule(rule, {"rs_spy": 1, "rs_sector": 1}))

    def test_nested_combinators(self) -> None:
        rule = {
            "all": [
                {"field": "gap_pct", "op": ">=", "value": 3},
                {
                    "any": [
                        {"field": "rs_spy", "op": ">=", "value": 3},
                        {"field": "rs_sector", "op": ">=", "value": 2},
                    ]
                },
            ]
        }
        self.assertTrue(evaluate_rule(rule, {"gap_pct": 3, "rs_sector": 2}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 1, "rs_sector": 2}))
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 3, "rs_sector": 1, "rs_spy": 1}))

    def test_empty_combinator_list_never_matches(self) -> None:
        self.assertFalse(evaluate_rule({"all": []}, {"gap_pct": 100}))
        self.assertFalse(evaluate_rule({"any": []}, {"gap_pct": 100}))


class RobustnessTests(unittest.TestCase):
    def test_unknown_field_never_matches_and_never_raises(self) -> None:
        rule = {"field": "totally_unknown_field", "op": ">=", "value": 1}
        self.assertFalse(evaluate_rule(rule, {"totally_unknown_field": 999}))

    def test_missing_value_in_snapshot_never_matches(self) -> None:
        rule = {"field": "gap_pct", "op": ">=", "value": 1}
        self.assertFalse(evaluate_rule(rule, {}))

    def test_none_value_in_snapshot_never_matches(self) -> None:
        rule = {"field": "gap_pct", "op": ">=", "value": 1}
        self.assertFalse(evaluate_rule(rule, {"gap_pct": None}))

    def test_empty_snapshot_never_matches(self) -> None:
        rule = {"all": [{"field": "gap_pct", "op": ">=", "value": 1}]}
        self.assertFalse(evaluate_rule(rule, {}))

    def test_unknown_operator_never_matches(self) -> None:
        rule = {"field": "gap_pct", "op": "!=", "value": 1}
        self.assertFalse(evaluate_rule(rule, {"gap_pct": 5}))

    def test_malformed_json_string_never_matches(self) -> None:
        self.assertFalse(evaluate_rule("{not valid json", {"gap_pct": 5}))

    def test_json_string_that_is_not_an_object_never_matches(self) -> None:
        self.assertFalse(evaluate_rule("[1, 2, 3]", {"gap_pct": 5}))

    def test_non_dict_rule_never_matches(self) -> None:
        self.assertFalse(evaluate_rule(None, {"gap_pct": 5}))
        self.assertFalse(evaluate_rule(42, {"gap_pct": 5}))  # type: ignore[arg-type]

    def test_non_dict_snapshot_never_matches(self) -> None:
        rule = {"field": "gap_pct", "op": ">=", "value": 1}
        self.assertFalse(evaluate_rule(rule, None))  # type: ignore[arg-type]

    def test_valid_json_string_rule_is_parsed(self) -> None:
        rule_json = '{"field": "gap_pct", "op": ">=", "value": 3}'
        self.assertTrue(evaluate_rule(rule_json, {"gap_pct": 5}))


class ValidateRuleStructureTests(unittest.TestCase):
    def test_valid_rule_has_no_errors(self) -> None:
        rule = {
            "all": [
                {"field": "gap_pct", "op": ">=", "value": 3},
                {"field": "perf_stock_1d", "op": ">", "value": 0},
            ],
            "opportunity_type": "GAP_AND_HOLD",
        }
        self.assertEqual(validate_rule_structure(rule), [])

    def test_unknown_field_is_reported(self) -> None:
        errors = validate_rule_structure({"field": "not_whitelisted", "op": ">=", "value": 1})
        self.assertTrue(any("not_whitelisted" in error for error in errors))

    def test_unknown_operator_is_reported(self) -> None:
        errors = validate_rule_structure({"field": "gap_pct", "op": "!=", "value": 1})
        self.assertTrue(any("!=" in error for error in errors))

    def test_between_without_two_bounds_is_reported(self) -> None:
        errors = validate_rule_structure({"field": "gap_pct", "op": "between", "value": [1]})
        self.assertTrue(errors)

    def test_nested_invalid_condition_is_reported(self) -> None:
        rule = {
            "all": [
                {"field": "gap_pct", "op": ">=", "value": 3},
                {"field": "bogus", "op": ">=", "value": 1},
            ]
        }
        errors = validate_rule_structure(rule)
        self.assertTrue(any("bogus" in error for error in errors))

    def test_not_an_object_is_reported(self) -> None:
        self.assertEqual(validate_rule_structure("not json"), ["rule must be a JSON object"])


if __name__ == "__main__":
    unittest.main()
