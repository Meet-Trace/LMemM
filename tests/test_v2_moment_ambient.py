import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "experiments", "moment_goals"))

import ambient


def moment(i, prompt="p", state="stable"):
    return {"id": f"m{i}", "revision": 1, "state": state, "prompt": prompt}


class SignatureTests(unittest.TestCase):
    def test_same_moments_same_signature(self):
        self.assertEqual(ambient.signature([moment(1), moment(2)]), ambient.signature([moment(1), moment(2)]))

    def test_new_moment_changes_signature(self):
        self.assertNotEqual(ambient.signature([moment(1)]), ambient.signature([moment(1), moment(2)]))

    def test_changed_prompt_changes_signature(self):
        self.assertNotEqual(ambient.signature([moment(1, "a")]), ambient.signature([moment(1, "b")]))


class CountTests(unittest.TestCase):
    def test_counts(self):
        with tempfile.TemporaryDirectory() as d:
            data = {"moments": [moment(1), moment(2), moment(3)],
                    "models": {"x": {"goals": [{"moment_id": "m1", "goal": "a"}, {"moment_id": "m2", "goal": ""}]},
                               "x (purpose)": {"goals": [{"moment_id": "m1", "goal": "p"}], "groups": [[0, 1], [2]]}}}
            json.dump(data, open(os.path.join(d, "moment_goals.json"), "w"))
            counts, moments = ambient.describe_counts(d, "x")
            self.assertEqual(counts, {"moments": 3, "described": 1, "purposes": 1, "activities": 1})
            self.assertEqual(len(moments), 3)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            counts, moments = ambient.describe_counts(d, "x")
            self.assertEqual(counts["moments"], 0)


class StatusTests(unittest.TestCase):
    def test_no_status_file(self):
        with tempfile.TemporaryDirectory() as d:
            args = mock.Mock(interval=60, data_dir=d)
            self.assertEqual(ambient.show_status(args, d), 1)

    def test_stale_status(self):
        with tempfile.TemporaryDirectory() as d:
            ambient.write_json(os.path.join(d, ambient.STATUS), {"updated": time.time() - 3600, "observer": {"running": True}})
            args = mock.Mock(interval=60, data_dir=d)
            with mock.patch("builtins.print") as printed:
                ambient.show_status(args, d)
            self.assertIn("STALE", printed.call_args_list[0][0][0])


class ObserverTests(unittest.TestCase):
    def test_lock_held_elsewhere_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            observer = ambient.Observer(d, d)
            process = mock.Mock()
            process.poll.return_value = 3
            process.returncode = 3
            observer.process = process
            observer.ensure()
            self.assertEqual(observer.state()["running"], "elsewhere")
            observer.log.close()


class OllamaTests(unittest.TestCase):
    def test_unreachable_host_is_reported(self):
        self.assertIn("not reachable", ambient.ollama_down("http://127.0.0.1:9"))


class PauseTests(unittest.TestCase):
    def test_low_battery_pauses(self):
        with mock.patch("run_vision.run", side_effect=["Now drawing from 'Battery Power'\n 10%; discharging", "free 50%"]):
            self.assertIn("battery", ambient.pause_reason(20, 10))

    def test_plugged_in_runs(self):
        with mock.patch("run_vision.run", side_effect=["Now drawing from 'AC Power'\n 10%; charging", "System-wide memory free percentage: 40%"]):
            self.assertIsNone(ambient.pause_reason(20, 10))


if __name__ == "__main__":
    unittest.main()
