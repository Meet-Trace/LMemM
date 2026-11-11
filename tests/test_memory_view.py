import unittest
from memory_view import evidence_rows,source_presentation
class ViewTests(unittest.TestCase):
    def test_rows_keep_citations_and_snapshot(self):
        rows=evidence_rows({'open_tasks':[{'statement':'TODO: Ship','at':'2026-10-10','citations':['c1']}], 'primary':[{'text':'retained','citation_id':'c2','at':'2026-10-10'}]})
        self.assertEqual(rows[0]['citation_id'],'c2')
        self.assertEqual(rows[0]['text'],'retained')
    def test_source_unavailable_and_current_file_are_distinct(self):
        v=source_presentation({'text':'old snapshot','locator':'/repo/a','at':'then','can_open_file':False})
        self.assertEqual(v['text'],'old snapshot');self.assertFalse(v['open_enabled'])
        self.assertIn('today',v['open_explanation'].lower())
    def test_evidence_preserves_provenance_and_truncation(self):
        rows=evidence_rows({'primary':[dict(text='excerpt',origin_type='observed_screen_text',truncated=True),dict(text='intentional',origin_type='user_note',truncated=False)]})
        self.assertEqual(rows[0]['provenance'],'Observed content');self.assertTrue(rows[0]['truncated'])
        self.assertEqual(rows[1]['provenance'],'Your note')
        detail=source_presentation(dict(text='excerpt',locator='/a',at='now',origin_type='observed_screen_text',truncated=True))
        self.assertTrue(detail['truncated']);self.assertEqual(detail['provenance'],'Observed content')


class FriendlyTextTests(unittest.TestCase):
    def test_dates_read_as_today_yesterday_or_day_month(self):
        from datetime import datetime, timezone
        from memory_view import friendly
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        tz = now.astimezone().tzinfo
        def local(day, hour):
            return datetime(2026, 10, day, hour, 5, tzinfo=tz).astimezone(timezone.utc).isoformat()
        self.assertEqual(friendly(local(12, 9), now).split(",")[0], "Today")
        self.assertEqual(friendly(local(11, 9), now).split(",")[0], "Yesterday")
        self.assertTrue(friendly(local(3, 9), now).startswith("3 Oct, "))
        self.assertEqual(friendly("not a date", now), "not a date")

    def test_long_file_names_are_cut_in_the_middle(self):
        from memory_view import short_name
        name = short_name("/Users/a/.lmemm-project-notes/native-3131876c15f4e10d0127af062170c0a1.md")
        self.assertLessEqual(len(name), 26)
        self.assertIn("…", name)
        self.assertTrue(name.endswith("c0a1.md"))
        self.assertEqual(short_name("/x/short.md"), "short.md")


class SearchNoteTests(unittest.TestCase):
    def rows(self, *texts):
        return [dict(section="Search results", text=t) for t in texts]

    def test_a_fitting_result_gets_no_note(self):
        from memory_view import search_note
        self.assertIsNone(search_note("clean context export", self.rows("a clean context export for an AI")))

    def test_weak_word_overlap_does_not_claim_semantic_results_are_unrelated(self):
        from memory_view import search_note
        note = search_note("a clean context export: what an AI should be handed", self.rows("def trail(): what a thing"))
        self.assertIn("Possible matches", note)
        self.assertNotIn("No match", note)
        self.assertNotIn("probably not", note)

    def test_database_paraphrase_does_not_discredit_saved_sqlite_note(self):
        from memory_view import search_note
        note=search_note('Why did we pick our database',self.rows('We decided to use SQLite because the data stays on this Mac'))
        self.assertNotIn('No match',note or '')
        self.assertNotIn('probably not',note or '')

    def test_native_note_without_a_file_explains_why_open_is_unavailable(self):
        from memory_view import source_presentation
        shown=source_presentation(dict(text='Saved note',locator='/p/.lmemm-project-notes/native.md',at='2026-10-20T12:43:00Z',origin_type='user_note',can_open_file=False))
        self.assertFalse(shown['open_enabled'])
        self.assertIn('no external file',shown['open_explanation'].lower())

    def test_nothing_found_says_so_and_why(self):
        from memory_view import search_note
        self.assertIn("No matches for", search_note("kubernetes ingress", []))
        self.assertIsNone(search_note("the a of", []))


class PaneColourTests(unittest.TestCase):
    """One blue accent; green, orange and grey only as state; never red (red is for a permission that is off)."""

    def test_state_colours_use_the_accent_and_never_red(self):
        import palette
        from memory_pane import ACC, SECTIONS, STATE_COLORS
        self.assertEqual(ACC, palette.ACCENT)
        self.assertEqual(STATE_COLORS['Processing'], palette.ACCENT)
        for colour in list(STATE_COLORS.values()) + [c for _t, _s, c in SECTIONS.values()]:
            self.assertFalse(colour[0] > 0.8 and colour[1] < 0.4, 'red-looking colour %r' % (colour,))

    def test_every_confirmation_has_a_title_an_explanation_and_a_button(self):
        from memory_view import CONFIRMS
        self.assertEqual(sorted(CONFIRMS), ['confirm_agent', 'confirm_disconnect', 'confirm_model'])
        for title, detail, go in CONFIRMS.values():
            self.assertTrue(title and detail and go)


class GroupEvidenceTests(unittest.TestCase):
    def rows(self):
        from memory_view import evidence_rows
        code = dict(origin_type='trusted_artifact_snapshot', locator='/p/src/trail_store.py')
        return evidence_rows({'primary': [
            dict(text='\n\n\ndef iso(dt):\n    return 1\n\n\n\nclass A:\n    pass', at='2026-10-15T10:00:00+00:00', citation_id='c1', **code),
            dict(text='other part', at='2026-10-15T10:05:00+00:00', citation_id='c2', **code),
            dict(text='We decided to use SQLite', at='2026-10-14T10:00:00+00:00', citation_id='c3', origin_type='user_note', locator='/p/.lmemm-project-notes/native-1.md')]})

    def test_one_card_per_file_with_a_count_and_the_latest_time(self):
        from memory_view import group_evidence
        out = group_evidence(self.rows())
        self.assertEqual(len(out), 2)
        self.assertEqual((out[0]['title'], out[0]['count'], out[0]['at']), ('trail_store.py', 2, '2026-10-15T10:05:00+00:00'))
        self.assertEqual(out[0]['citation_id'], 'c1')                  # the best match is the one shown

    def test_notes_are_never_grouped_or_retitled(self):
        from memory_view import group_evidence
        note = group_evidence(self.rows())[1]
        self.assertIsNone(note['title'])
        self.assertEqual(note['text'], 'We decided to use SQLite')

    def test_excerpt_drops_leading_blank_lines_folds_gaps_and_caps_length(self):
        from memory_view import excerpt
        self.assertEqual(excerpt('\n\n\ndef a():\n    pass\n\n\n\nclass B:'), 'def a():\n    pass\n\nclass B:')
        long = '\n'.join('line %d' % i for i in range(20))
        self.assertTrue(excerpt(long).endswith('\n…'))
        self.assertEqual(len(excerpt('x' * 500).rstrip('…')), 109)


class SettingsCopyTests(unittest.TestCase):
    def test_confirmations_say_trace_and_use_no_em_dash(self):
        from memory_view import CONFIRMS
        for title, detail, button in CONFIRMS.values():
            for text in (title, detail, button):
                self.assertNotIn("LMemM", text)
                self.assertNotIn("—", text)

    def test_settings_copy_in_the_pane_uses_no_em_dash(self):
        import os
        source = open(os.path.join(os.path.dirname(__file__), "..", "src", "memory_pane.py")).read()
        settings = source.split("def _settings")[1]
        self.assertNotIn("—", settings)
        self.assertNotIn("LMemM", settings.split("self.bottom")[0].replace("LMemM:", ""))


class SettingsButtonSystemTests(unittest.TestCase):
    def test_settings_buttons_all_go_through_the_shared_helper(self):
        import os
        source = open(os.path.join(os.path.dirname(__file__), "..", "src", "memory_pane.py")).read()
        settings = source.split("def _settings")[1]
        self.assertIn("BTN_H,BTN_PAD=32,34", source)                    # module level: shared with the Memory page
        self.assertEqual(settings.count("kit.button("), 2)              # the helper, and the ellipsis menu button
        self.assertIn("'destructive':'danger'", source)
        kit = open(os.path.join(os.path.dirname(__file__), "..", "src", "setup_kit.py")).read()
        self.assertIn('kind == "danger"', kit)


class SettingsActionColumnTests(unittest.TestCase):
    def test_one_action_width_and_no_back_to_memory(self):
        import os
        base = os.path.join(os.path.dirname(__file__), "..", "src")
        pane = open(os.path.join(base, "memory_pane.py")).read().split("def _settings")[1]
        self.assertIn("ACTION_W=148", pane)
        self.assertIn("action_x=x+w-PADX-SLOT-ACTION_W", pane)          # every row starts its action at the same x
        self.assertNotIn("Back to Memory", open(os.path.join(base, "window.py")).read())


class MemoryPageTests(unittest.TestCase):
    def test_default_cards_show_no_provenance_label_and_details_sit_behind_a_toggle(self):
        import os
        source = open(os.path.join(os.path.dirname(__file__), "..", "src", "memory_pane.py")).read()
        page = source.split("def render")[1].split("def _fade_in")[0]
        self.assertNotIn("row['provenance']", page)                       # "Your note" and friends are not on the card
        self.assertIn("'toggle_details'", page)
        self.assertIn("'toggle_composer'", page)
        win = open(os.path.join(os.path.dirname(__file__), "..", "src", "window.py")).read()
        self.assertIn("'toggle_details','toggle_composer'", win)


class SourceProvenanceTests(unittest.TestCase):
    def test_browser_capture_shows_page_facts_not_the_note_text(self):
        from memory_view import source_provenance
        p = source_provenance(dict(origin_type='browser_selection', title='Pricing – Acme', url='https://www.acme.com/pricing?x=1',
                                   text='SECRET BODY', locator='', at='2026-11-10T10:00:00Z', source_id='s1', citation_id='c1'))
        self.assertEqual(p['kind'], 'Saved from browser')
        self.assertEqual(p['heading'], 'Pricing – Acme')
        self.assertIn(('Site', 'acme.com'), p['facts'])
        self.assertNotIn('SECRET BODY', str(p))
        self.assertIn(('Source id', 's1'), p['details'])
        self.assertFalse(any('s1' in v for _l, v in p['facts']))

    def test_standalone_note_says_so_and_invents_nothing(self):
        from memory_view import source_provenance
        p = source_provenance(dict(origin_type='user_note', text='x', locator='', at='2026-11-10T10:00:00Z'))
        self.assertEqual(p['kind'], 'Project note')
        self.assertTrue(any('standalone saved note' in v for _l, v in p['facts']))
        self.assertEqual(p['url'], '')


class ResumeItemTests(unittest.TestCase):
    def test_priority_is_task_then_decision_then_note(self):
        from memory_view import resume_item
        note = dict(text='a note', at='2026-11-10T09:00:00Z', citation_id='n')
        dec = dict(statement='We chose X', at='2026-11-09T09:00:00Z', citations=['d'])
        t_old = dict(id='t1', statement='Old task', at='2026-11-01T09:00:00Z', citations=['t1c'], native_note_id=None)
        t_new = dict(id='t2', statement='New task\nsecond line', at='2026-11-05T09:00:00Z', citations=['t2c'], native_note_id=None)
        item = resume_item(dict(open_tasks=[t_old, t_new], decisions=[dec], saved_activity=[note]))
        self.assertEqual((item['kind'], item['title'], item['citation_id']), ('Next action', 'New task', 't2c'))
        self.assertEqual(item['done'], ('set_task_state', {'claim_id': 't2', 'done': True}))
        self.assertEqual(resume_item(dict(decisions=[dec], saved_activity=[note]))['kind'], 'Decision')
        self.assertEqual(resume_item(dict(saved_activity=[note]))['kind'], 'Note')
        self.assertIsNone(resume_item(dict()))

    def test_a_native_note_task_is_completed_as_a_note(self):
        from memory_view import resume_item
        item = resume_item(dict(open_tasks=[dict(id='t', statement='Do it', at='x', citations=['c'], native_note_id='nn')]))
        self.assertEqual(item['done'], ('complete_native_note', {'note_id': 'nn', 'done': True}))
