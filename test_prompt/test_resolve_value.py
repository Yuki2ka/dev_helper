from __future__ import annotations

import unittest
import random
import re
from typing import Any

# Module-level globals for resolve_value to access
PROMPT = "default_prompt_value"
CURRENT_DATE = "2024-01-01"


def resolve_value(val: Any) -> str:
    """Copied from ComfyUI_workflow_api.py to test its behavior."""
    res = str(val()) if callable(val) else str(val)

    def replace_var(match):
        var_name = match.group(1)
        return str(globals().get(var_name, match.group(0)))

    return re.sub(r"\{(\w+)\}", replace_var, res)


class TestResolveValueLambda(unittest.TestCase):
    def test_lambda_callable_executed(self):
        """Lambda should be called and result converted to string."""
        result = resolve_value(lambda: "dynamic value")
        self.assertEqual(result, "dynamic value")

    def test_lambda_with_random(self):
        """Lambda using random.randint should return a number string."""
        result = resolve_value(lambda: f"{random.randint(1, 5)} trees")
        self.assertTrue(result.endswith("trees"))
        num = int(result.split()[0])
        self.assertGreaterEqual(num, 1)
        self.assertLessEqual(num, 5)

    def test_variable_interpolation(self):
        """{VARNAME} should be replaced with global variable value."""
        result = resolve_value("{PROMPT} with suffix")
        self.assertEqual(result, "default_prompt_value with suffix")

    def test_lambda_with_variable_interpolation(self):
        """Lambda result should have variables interpolated."""
        # This tests lambda returning a string with {VAR} that gets interpolated
        # Note: The lambda runs first, then interpolation happens on the result
        # The {PROMPT} inside lambda string is resolved because PROMPT is in globals
        result = resolve_value(lambda: "abstract {PROMPT} maze")
        self.assertEqual(result, "abstract default_prompt_value maze")

    def test_nonexistent_variable_unchanged(self):
        """Non-existent variable should remain unchanged in output."""
        result = resolve_value("static text {NONEXISTENT} more text")
        self.assertEqual(result, "static text {NONEXISTENT} more text")

    def test_static_string_passthrough(self):
        """Static string without {var} should pass through unchanged."""
        result = resolve_value("just plain text")
        self.assertEqual(result, "just plain text")

    def test_mixed_variables_in_lambda(self):
        """Lambda with multiple {VAR} placeholders should all be resolved."""
        result = resolve_value(lambda: "{PROMPT} on {CURRENT_DATE}")
        self.assertEqual(result, "default_prompt_value on 2024-01-01")


class TestUniversalReplaceLambda(unittest.TestCase):
    def test_search_value_and_replace_lambda(self):
        """SEARCH_VALUE_AND_REPLACE with lambda should work in universal_replace."""
        SEARCH_VALUE_AND_REPLACE = {
            "##1": lambda: f"forest with {random.randint(1, 10)} trees",
        }

        workflow = {"1": {"inputs": {"text": "placeholder ##1 here"}}}

        # Simulate walk_and_replace_text logic (recursive)
        def walk_and_replace(obj):
            nonlocal counter
            if isinstance(obj, dict):
                for key, value in list(obj.items()):
                    if isinstance(value, str):
                        for search_str, replace_val in SEARCH_VALUE_AND_REPLACE.items():
                            if search_str in value:
                                obj[key] = value.replace(search_str, resolve_value(replace_val))
                                counter += 1
                    else:
                        walk_and_replace(value)
            elif isinstance(obj, list):
                for item in obj:
                    walk_and_replace(item)

        counter = 0
        walk_and_replace(workflow)

        self.assertEqual(counter, 1)
        self.assertIn("forest with", workflow["1"]["inputs"]["text"])
        self.assertIn("trees", workflow["1"]["inputs"]["text"])

    def test_search_property_name_and_replace_value_lambda(self):
        """SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE with lambda should work in universal_replace."""
        SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE = {
            "inputs/text": lambda: f"prompt with {random.randint(5, 15)} birds",
        }

        workflow = {
            "1": {"inputs": {"text": "original"}},
            "2": {"inputs": {"other": "value"}},
        }

        # Simulate global replacement logic
        global_paths = {}
        for path, new_val in SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE.items():
            parts = path.split('/')
            if not parts[0].isdigit():
                global_paths[path] = new_val

        for path, new_val in global_paths.items():
            resolved_new_val = resolve_value(new_val)
            parts = path.split('/')
            for node_id, node_data in workflow.items():
                if not node_id.isdigit():
                    continue
                curr = node_data
                valid_path = True
                for step in parts:
                    if isinstance(curr, dict) and step in curr:
                        curr = curr[step]
                    else:
                        valid_path = False
                        break
                if valid_path:
                    target_parent = node_data
                    for step in parts[:-1]:
                        target_parent = target_parent[step]
                    target_parent[parts[-1]] = resolved_new_val

        self.assertIn("prompt with", workflow["1"]["inputs"]["text"])
        self.assertIn("birds", workflow["1"]["inputs"]["text"])
        self.assertEqual(workflow["2"]["inputs"]["other"], "value")

    def test_combined_static_and_lambda_replacements(self):
        """Mix of static and lambda replacements should both work (both counted, last wins)."""
        SEARCH_VALUE_AND_REPLACE = {
            "static": "REPLACED_STATIC",
            "dyn": lambda: f"value_{random.randint(1, 100)}",
        }

        workflow = {"1": {"inputs": {"text": "prefix static dyn suffix"}}}

        # Simulate walk_and_replace_text logic (recursive)
        # Note: Original code applies each replacement to the ORIGINAL value,
        # so later replacements overwrite earlier ones on same key
        def walk_and_replace(obj):
            nonlocal counter, original_value
            if isinstance(obj, dict):
                for key, value in list(obj.items()):
                    if isinstance(value, str):
                        original_value = value
                        for search_str, replace_val in SEARCH_VALUE_AND_REPLACE.items():
                            if search_str in original_value:
                                obj[key] = original_value.replace(search_str, resolve_value(replace_val))
                                counter += 1
                    else:
                        walk_and_replace(value)
            elif isinstance(obj, list):
                for item in obj:
                    walk_and_replace(item)

        counter = 0
        original_value = ""
        walk_and_replace(workflow)

        # Both replacements are counted
        self.assertEqual(counter, 2)
        # Last replacement ("dyn") overwrites the value
        self.assertRegex(workflow["1"]["inputs"]["text"], r"prefix static value_\d+ suffix")

    def test_specific_path_replacement_with_lambda(self):
        """Path with node_id first should target specific node only."""
        SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE = {
            "1/inputs/text": lambda: f"specific lambda value {random.randint(1, 5)}",
        }

        workflow = {
            "1": {"inputs": {"text": "original"}},
            "2": {"inputs": {"text": "original"}},
        }

        # Simulate specific path replacement logic
        specific_paths = {}
        for path, new_val in SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE.items():
            parts = path.split('/')
            if parts[0].isdigit():
                specific_paths[path] = new_val

        for path, new_val in specific_paths.items():
            resolved_new_val = resolve_value(new_val)
            parts = path.split('/')
            node_id, remaining_path = parts[0], parts[1:]
            if node_id in workflow:
                curr = workflow[node_id]
                valid_path = True
                for step in remaining_path:
                    if isinstance(curr, dict) and step in curr:
                        curr = curr[step]
                    else:
                        valid_path = False
                    if not valid_path:
                        break
                if valid_path:
                    target_parent = workflow[node_id]
                    for step in remaining_path[:-1]:
                        target_parent = target_parent[step]
                    target_parent[remaining_path[-1]] = resolved_new_val

        self.assertIn("specific lambda value", workflow["1"]["inputs"]["text"])
        self.assertEqual(workflow["2"]["inputs"]["text"], "original")


if __name__ == "__main__":
    unittest.main()