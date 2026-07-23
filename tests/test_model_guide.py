from claude_sessions import config as c


def test_cost_bar_scales_with_pricing():
    assert c.cost_bar('claude-haiku-4-5') == '$'
    assert c.cost_bar('claude-sonnet-5') == '$$'
    assert c.cost_bar('claude-opus-4-8') == '$$$'
    assert c.cost_bar('claude-fable-5') == '$$$$$'
    assert c.cost_bar('') == ''
    assert len(c.cost_bar('claude-opus-4-8')) >= len(c.cost_bar('claude-sonnet-5'))


def test_cap_bar():
    assert c.cap_bar('claude-haiku-4-5') == '▪▪'
    assert len(c.cap_bar('claude-opus-4-8')) == 5
    assert c.cap_bar('') == ''


def test_advisor_flags_suboptimal_and_confirms_good():
    lvl, msg = c.advise('claude-opus-4-8', 'low')
    assert lvl == 'tip' and 'Sonnet 5' in msg           # names the better option
    assert c.advise('claude-sonnet-5', 'xhigh')[0] == 'warn'
    assert 'Opus' in c.advise('claude-sonnet-5', 'xhigh')[1]
    assert c.advise('claude-haiku-4-5', 'max')[0] == 'warn'
    assert c.advise('claude-fable-5', 'low')[0] == 'tip'
    assert c.advise('claude-sonnet-5', 'high')[0] == 'ok'
    assert c.advise('claude-opus-4-8', 'xhigh')[0] == 'ok'
    assert c.advise('', '')[0] == 'tip'                 # no model -> nudge default


def test_presets_task_based_and_valid():
    names = [n for n, _d, _f in c.LAUNCH_PRESETS]
    assert names[0] == 'Recommended'
    assert names == ['Recommended', 'Cheap & fast', 'Deep reasoning', 'Max capability']
    for _name, desc, fields in c.LAUNCH_PRESETS:
        assert desc                                     # each has a description
        if 'model' in fields:
            assert fields['model'] in c.MODELS
        if 'effort' in fields:
            assert fields['effort'] in c.EFFORTS
        if 'subagent_model' in fields:
            assert fields['subagent_model'] in c.MODELS


def test_active_preset_roundtrip():
    name, _desc, fields = c.LAUNCH_PRESETS[0]
    assert c.active_preset(dict(fields)) == name
    assert c.active_preset({'model': '', 'effort': ''}) is None


def test_model_card_rows_covers_roster_with_swe():
    rows = c.model_card_rows()
    assert [r[0] for r in rows] == [m for m in c.MODELS if m]
    for _mid, _lbl, cb, capb, bf, sw in rows:
        assert cb and capb and bf and sw


def test_frontier_rows_are_curated_good_combos_in_ascending_power():
    rows = c.frontier_rows()
    assert [(m, e) for m, e, *_ in rows] == c.MODEL_EFFORT_FRONTIER
    # every stop is one the advisor itself rates 'ok' -- a bad combo can't
    # be reached from the frontier slider
    for model, effort, _label, _cost, _swe, _note in rows:
        assert c.advise(model, effort)[0] == 'ok'
    # cheapest→most powerful: haiku first, fable last
    assert rows[0][0] == 'claude-haiku-4-5'
    assert rows[-1][0] == 'claude-fable-5' and rows[-1][1] == 'max'
    # each row carries a non-empty label/cost/note for the GUI read-out
    for _model, _effort, label, cost, _swe, note in rows:
        assert label and cost and note
