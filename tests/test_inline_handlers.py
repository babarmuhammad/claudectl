r"""A value going into an inline handler crosses two parsers, not one.

The markup writes `onchange="ccSet('k',this,'${esc(a.dir)}')"`. The browser
unescapes the ATTRIBUTE first, and then JS parses what is left as a string
literal — so a Windows path's backslashes are read as escape sequences:
`C:\Users\mab\.claude` becomes `C:Usersmab.claude`. The settings editor posted
that mangled value as `cfgdir`, the account allowlist correctly refused it, and
every save came back "invalid cfgdir".

That was the first symptom. The same two-parser mistake is also a security bug,
and the file had it 62 times:

  * `${JSON.stringify(v)}` inside `onclick='…'` escapes for JS and NOTHING for
    HTML. One apostrophe in a filename, an MCP server name, a git branch or a
    skill directory closes the attribute and everything after it is parsed as
    new attributes — `x' onmouseover='…` is script execution, same-origin, with
    the API token in scope.
  * `'${jsq(v)}'` inside `on…="…"` is the mirror image: `jsq` HTML-escaped `'`
    to `&#39;`, which the parser hands back to JS as a real `'`, ending the
    string literal. A model-written suggestion containing "don't" broke the
    brief page outright.

`hesc()` is the only correct helper for the position, because it applies both
layers in the right order — `JSON.stringify` first (a valid JS literal, with
backslashes doubled and quotes escaped), `esc` second (so that literal survives
the HTML parser). It emits its own quotes, so the markup must never write them.
"""
import re

from claude_sessions.gui_html import PAGE

_JS = PAGE[PAGE.index('<script>'):]


def test_no_value_is_interpolated_into_a_handler_as_a_bare_js_literal():
    """`'${expr}'` inside an on*= attribute is the wrong layer, always."""
    bad = [m.group(0)[:80] for m in
           re.finditer(r'on\w+="[^"]*?\'\$\{[^}]*\}\'', _JS)]
    bad += [m.group(0)[:80] for m in
            re.finditer(r'on\w+=\'[^\']*?"\$\{[^}]*\}"', _JS)]
    assert not bad, 'quote the value with hesc() instead: %s' % bad


def test_json_stringify_never_reaches_an_html_attribute():
    """The security half. JSON.stringify escapes for JS and not for HTML."""
    bad = [m.group(0)[:80] for m in
           re.finditer(r'on\w+=[\'"][^\'"]*?JSON\.stringify', _JS)]
    assert not bad, 'use hesc(), not a bare JSON.stringify: %s' % bad


def test_the_helper_applies_both_layers_in_the_right_order():
    line = _JS[_JS.index('function hesc('):]
    line = line[:line.index('\n')]
    assert 'JSON.stringify' in line, 'hesc no longer produces a JS literal'
    assert line.index('esc(') < line.index('JSON.stringify'), \
        'esc must wrap JSON.stringify, not the other way round'


def test_nobody_open_codes_the_backslash_escape_any_more():
    """One helper, or it is a convention again. JSON.stringify does this."""
    assert _JS.count(r"replace(/\\/g,'\\\\')") == 0, \
        'the backslash escape is open-coded outside hesc()'


def test_the_settings_editor_passes_an_account_index_not_a_path():
    """Better than escaping: the value never enters the markup at all."""
    assert 'onchange="ccSet(' in _JS
    m = re.search(r'onchange="ccSet\([^"]*\)"', _JS)
    assert m and not re.search(r'\b(dir|path|file|cfgdir)\b', m.group(0)), \
        m.group(0) if m else 'missing'
    assert 'const a=CCACCTS[ai];' in _JS, 'ccSet no longer resolves by index'
