"""Test that the built GUI HTML contains flicker-prevention structures."""
from claude_sessions.gui_html import PAGE


def test_poll_uses_cache_vars():
    """The poll() function inside runJob must skip DOM writes when values unchanged."""
    assert '__plMsgs' in PAGE, 'expected __plMsgs cache variable in poll()'
    assert '__plSub' in PAGE, 'expected __plSub cache variable in poll()'
    assert '__plLabel' in PAGE, 'expected __plLabel cache variable in poll()'
    assert 'msgsHtml!==__plMsgs' in PAGE, 'expected conditional DOM update for messages'
    assert 'innerHTML=msgsHtml' in PAGE, 'expected innerHTML for messages only'


def test_poll_uses_textcontent():
    """The poll() function should use textContent, not innerHTML, for label/sub fields."""
    assert "textContent=st.label" in PAGE, 'expected textContent for label'
    assert "textContent=sub" in PAGE, 'expected textContent for sub (elapsed)'


def test_loading_bar_exists():
    """The loading bar infrastructure must be present."""
    assert 'id="loading"' in PAGE, 'expected loading bar element'
    assert 'setLoading' in PAGE, 'expected setLoading() function'
    assert '__loadingCount' in PAGE, 'expected __loadingCount variable'


def test_escape_closes_modals():
    """Escape key handler must close all modals."""
    assert "'Escape'" in PAGE, 'expected Escape key handler'
    assert "classList.remove('show')" in PAGE, 'expected class-based modal close'


def test_modals_have_aria():
    """Modals should have dialog role for accessibility."""
    assert 'role="dialog"' in PAGE, 'expected role="dialog" on modals'
    assert 'aria-modal="true"' in PAGE, 'expected aria-modal="true" on modals'
