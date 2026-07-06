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

    def test_in_membership(self) -> None:
        rule = {
            "field": "time_bucket",
            "op": "in",
            "value": ["OPEN", "MORNING", "AFTERNOON", "POWER_HOUR"],
        }
        self.assertTrue(evaluate_rule(rule, {"time_bucket": "OPEN"}))
        self.assertTrue(evaluate_rule(rule, {"time_bucket": "POWER_HOUR"}))
        self.assertFalse(evaluate_rule(rule, {"time_bucket": "LUNCH"}))
        self.assertFalse(evaluate_rule(rule, {"time_bucket": "OFF_HOURS"}))

    def test_in_is_case_sensitive(self) -> None:
        rule = {"field": "time_bucket", "op": "in", "value": ["OPEN"]}
        self.assertFalse(evaluate_rule(rule, {"time_bucket": "open"}))

    def test_in_empty_list_never_matches(self) -> None:
        rule = {"field": "time_bucket", "op": "in", "value": []}
        self.assertFalse(evaluate_rule(rule, {"time_bucket": "OPEN"}))

    def test_in_non_list_value_never_matches(self) -> None:
        rule = {"field": "time_bucket", "op": "in", "value": "OPEN"}
        self.assertFalse(evaluate_rule(rule, {"time_bucket": "OPEN"}))

    def test_in_missing_field_never_matches(self) -> None:
        rule = {"field": "time_bucket", "op": "in", "value": ["OPEN"]}
        self.assertFalse(evaluate_rule(rule, {}))
        self.assertFalse(evaluate_rule(rule, {"time_bucket": None}))

    def test_in_boolean_actual_never_matches(self) -> None:
        rule = {"field": "price_above_ema20", "op": "in", "value": ["True"]}
        self.assertFalse(evaluate_rule(rule, {"price_above_ema20": True}))


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


class F1FieldTruthTableTests(unittest.TestCase):
    """Truth table for the F1 whitelist entries (TODO 7.8)."""

    def test_rvol_canonical_field_and_aliases(self) -> None:
        rule = {"field": "rvol", "op": ">=", "value": 1.5}
        self.assertTrue(evaluate_rule(rule, {"rvol": 1.5}))
        self.assertFalse(evaluate_rule(rule, {"rvol": 1.49}))
        # Legacy aliases stay resolvable so migrated data keeps matching.
        self.assertTrue(evaluate_rule(rule, {"relative_volume": 1.6}))
        self.assertTrue(evaluate_rule(rule, {"volume_ratio": 1.6}))
        self.assertTrue(evaluate_rule(rule, {"volume_ratio_15m": 1.6}))
        # Canonical field wins over aliases.
        self.assertFalse(evaluate_rule(rule, {"rvol": 1.0, "volume_ratio": 9.9}))

    def test_atr_pct(self) -> None:
        rule = {"field": "atr_pct", "op": "between", "value": [0.5, 5]}
        self.assertTrue(evaluate_rule(rule, {"atr_pct": 2.0}))
        self.assertFalse(evaluate_rule(rule, {"atr_pct": 6.0}))
        self.assertFalse(evaluate_rule(rule, {}))

    def test_dist_vwap_pct_signed(self) -> None:
        rule = {"field": "dist_vwap_pct", "op": ">=", "value": 0}
        self.assertTrue(evaluate_rule(rule, {"dist_vwap_pct": 0}))
        self.assertTrue(evaluate_rule(rule, {"dist_vwap_pct": 1.2}))
        self.assertFalse(evaluate_rule(rule, {"dist_vwap_pct": -0.1}))
        self.assertFalse(evaluate_rule(rule, {}))

    def test_time_bucket_equality(self) -> None:
        rule = {"field": "time_bucket", "op": "==", "value": "LUNCH"}
        self.assertTrue(evaluate_rule(rule, {"time_bucket": "LUNCH"}))
        self.assertFalse(evaluate_rule(rule, {"time_bucket": "OPEN"}))
        self.assertFalse(evaluate_rule(rule, {}))

    def test_daily_trend_booleans(self) -> None:
        for field in ("price_above_ema20", "price_above_sma50"):
            rule = {"field": field, "op": "==", "value": True}
            self.assertTrue(evaluate_rule(rule, {field: True}))
            self.assertFalse(evaluate_rule(rule, {field: False}))
            # Missing daily enrichment -> None -> non-match, never an error.
            self.assertFalse(evaluate_rule(rule, {field: None}))
            self.assertFalse(evaluate_rule(rule, {}))


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

    def test_in_without_list_value_is_reported(self) -> None:
        errors = validate_rule_structure({"field": "time_bucket", "op": "in", "value": "OPEN"})
        self.assertTrue(errors)
        errors_empty = validate_rule_structure({"field": "time_bucket", "op": "in", "value": []})
        self.assertTrue(errors_empty)

    def test_in_with_list_value_is_valid(self) -> None:
        rule = {"field": "time_bucket", "op": "in", "value": ["OPEN", "MORNING"]}
        self.assertEqual(validate_rule_structure(rule), [])

    def test_f1_fields_are_whitelisted(self) -> None:
        for field in (
            "rvol",
            "atr_pct",
            "dist_vwap_pct",
            "time_bucket",
            "price_above_ema20",
            "price_above_sma50",
        ):
            rule = {"field": field, "op": "==", "value": 1}
            self.assertEqual(validate_rule_structure(rule), [], field)

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
