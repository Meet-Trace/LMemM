"""A repeat look at identical work must not change groups because Python reorders sets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CODE = """
import json, sys
import project_reasoner_assign as assign
import project_reasoner_graph as graph
import synthetic_days
if sys.argv[1] == 'tied-names':
    doc = {'items': {i: {'id': i, 'kind': 'document', 'title': 'Zebra Alpha Delta Gamma'} for i in ('a', 'b')},
           'timeline': [{'item': 'a', 'from': '2026-11-09T10:00:00', 'to': '2026-11-09T10:01:00'},
                        {'item': 'b', 'from': '2026-11-09T10:01:00', 'to': '2026-11-09T10:02:00'}]}
    doc['items'].update({f'x{n}': {'id': f'x{n}', 'kind': 'document', 'title': f'unrelated {n}'} for n in range(12)})
elif sys.argv[1] == 'synthetic':
    doc = synthetic_days.generate(12, 77)
else:
    with open('tests/fixtures/project_grouping/sample_day.json') as fh:
        doc = json.load(fh)
model = (lambda system, user, schema: {'names': ['Zebra Alpha Delta Gamma']}) if sys.argv[1] == 'tied-names' else graph.CapitalisedNames()
assigned, projects, _ = assign.run(doc['items'], doc['timeline'], model)
print(json.dumps({'assigned': assigned, 'projects': projects}, sort_keys=True))
"""

class GroupingRepeatabilityTests(unittest.TestCase):
    def test_same_work_keeps_memberships_and_names_across_hash_seeds(self):
        for day in ('sample', 'synthetic', 'tied-names'):
            with self.subTest(day=day):
                results = [json.loads(subprocess.check_output(
                    [sys.executable, '-c', CODE, day], cwd=ROOT,
                    env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONHASHSEED': str(seed)},
                    text=True, timeout=30)) for seed in (1, 2, 3)]
                if day == 'tied-names':
                    self.assertTrue(results[0]['projects'], 'The tie fixture must actually form a project')
                for result in results[1:]:
                    self.assertEqual(results[0], result)
