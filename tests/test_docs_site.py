"""Gates for the MkDocs site.

None of these need mkdocs installed — they read text files only, so they ride the
existing `test` job across the whole OS/Python matrix. `mkdocs build --strict`
in the `docs` job covers what actually needs the toolchain.
"""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
# The manual moved to its own subdomain; the apex is the marketing site and is a
# different deployment. test_every_docs_page_the_readme_links_to_exists derives the
# slugs it checks from this, so it now covers the README's docs links only — links
# to pages that belong to the apex are deliberately out of its scope.
SITE_URL = 'https://docs.claudectl.space/'

# Moved to notes/ so they are outside docs_dir. Inside it they would be built and
# listed in sitemap.xml even without a nav entry.
INTERNAL_NOTES = ('gui-rework-notes.md', 'plan-execute-audit.md', 'research-2026-08.md')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _mkdocs():
    return _read(os.path.join(ROOT, 'mkdocs.yml'))


def _nav_pages():
    """Filenames in the nav block. Regex, not PyYAML — the `test` job installs
    only pytest, the same reason test_packaging.py hand-scans pyproject.toml."""
    return set(re.findall(r':\s*([\w./-]+\.md)\s*$', _mkdocs(), re.M))


def _doc_pages():
    out = set()
    for dirpath, _dirs, files in os.walk(DOCS):
        for name in files:
            if name.endswith('.md'):
                rel = os.path.relpath(os.path.join(dirpath, name), DOCS)
                out.add(rel.replace(os.sep, '/'))
    return out


def test_every_snippet_a_page_pulls_in_resolves():
    """A page including `--8<-- "FILE:section"` renders EMPTY when the marker
    moves — no error, no broken link, nothing for --strict to catch.

    This used to assert the source was always README.md, back when the README WAS
    the site and every page was a nine-line include stub. That direction is
    inverted now (the pages hold the content and the README points at them), so
    the gate is stated against whatever file an include actually names —
    today only docs/changelog.md, pulling the root CHANGELOG.md."""
    for rel in _doc_pages():
        for src, section in re.findall(r'--8<--\s+"([^":]+)(?::([\w-]+))?"',
                                       _read(os.path.join(DOCS, rel))):
            path = os.path.join(ROOT, src.replace('/', os.sep))
            assert os.path.isfile(path), '%s includes missing %s' % (rel, src)
            if section:
                text = _read(path)
                assert '[start:%s]' % section in text, \
                    '%s wants %s:%s — no start marker' % (rel, src, section)
                assert '[end:%s]' % section in text, \
                    '%s wants %s:%s — no end marker' % (rel, src, section)


# The H2s that moved out to their own pages. A reader who lands on the repo should
# see the pitch; the manual is the site's job.
MOVED_OUT = ('## Features', '## Install', '## Usage', '## Reference',
             '## Troubleshooting')


def test_the_readme_stays_a_pitch_and_does_not_grow_back_into_a_manual():
    """It was 907 lines — Features + Install + Usage + Reference alone were 78% of
    it, and someone evaluating the repo had to scroll a manual to find out what it
    was. Nothing stops that creeping back one section at a time, which is what this
    counts."""
    readme = _read(os.path.join(ROOT, 'README.md'))
    lines = len(readme.splitlines())
    assert lines < 200, 'README is %d lines — the manual is moving back in' % lines
    back = [h for h in MOVED_OUT if h in readme]
    assert not back, 'these belong on the docs site, not the README: %s' % back


def test_every_docs_page_the_readme_links_to_exists():
    """The README is now a map. Absolute site URLs are invisible to
    `mkdocs build --strict`, so a page renamed here 404s in production with
    nothing failing anywhere."""
    readme = _read(os.path.join(ROOT, 'README.md'))
    slugs = set(re.findall(re.escape(SITE_URL) + r'([\w-]+)/', readme))
    assert slugs, 'the README stopped pointing at the docs site'
    missing = [s for s in sorted(slugs) if s + '.md' not in _doc_pages()]
    assert not missing, 'README links at missing pages: %s' % missing


def test_no_internal_dev_note_sits_inside_the_docs_dir():
    """docs_dir is the whole of docs/, so anything dropped in it is published."""
    for name in INTERNAL_NOTES:
        assert not os.path.exists(os.path.join(DOCS, name)), \
            '%s is inside docs_dir and would be published' % name
        assert os.path.isfile(os.path.join(ROOT, 'notes', name)), \
            '%s vanished instead of moving to notes/' % name


def test_every_markdown_page_under_docs_is_in_the_nav():
    """The cost of sharing docs_dir with generated output: a stray .md is a live
    public page. Being absent from nav does not stop it building or being
    sitemapped, so require every page to be declared."""
    assert _doc_pages() == _nav_pages(), \
        'nav and docs/ disagree: %s' % sorted(_doc_pages() ^ _nav_pages())


def test_the_nav_only_references_pages_that_exist():
    for page in _nav_pages():
        assert os.path.isfile(os.path.join(DOCS, page)), 'nav points at missing %s' % page


def test_site_url_is_set_because_canonicals_and_the_sitemap_derive_from_it():
    assert 'site_url: %s' % SITE_URL in _mkdocs()
    assert SITE_URL in _read(os.path.join(DOCS, 'robots.txt'))


def test_the_site_sources_are_tracked_by_git():
    """A page that exists locally but was never committed builds fine here and
    404s in production. Same failure the plugin bundle already guards against."""
    want = [os.path.join('docs', 'index.md'), os.path.join('docs', 'usage.md'),
            os.path.join('docs', 'compare.md'), os.path.join('mkdocs.yml'),
            os.path.join('overrides', 'main.html'),
            os.path.join('docs', 'stylesheets', 'extra.css'),
            os.path.join('docs', 'assets', 'favicon.ico'),
            os.path.join('docs', 'assets', 'og-card.png')]
    r = subprocess.run(['git', 'check-ignore'] + want, cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='ignore', timeout=60)
    assert r.returncode != 0, 'gitignored site files: %s' % r.stdout
    for rel in want:
        assert os.path.isfile(os.path.join(ROOT, rel)), rel


def test_no_redirect_shadows_a_page_that_still_exists():
    """Vercel evaluates `redirects` BEFORE serving a static file, so a redirect on a
    live path makes that page unreachable — the built HTML is never consulted.

    The trap is specific and was hit while writing these rules: `/usage/` used to be
    the TUI manual, which is now `/tui/`, so the obvious rule is
    `/usage/ -> /tui/`. But the token-economy guide MOVED ONTO `/usage/`, so that
    rule would hide it and would also swallow `/token-economy/ -> /usage/` one hop
    later. A renamed path may only be redirected if nothing was renamed back onto it."""
    import json
    v = json.loads(_read(os.path.join(ROOT, 'vercel.json')))
    pages = {'/%s/' % p[:-3] for p in _doc_pages()}
    pages.add('/')  # index.md
    bad = [r['source'] for r in v.get('redirects', [])
           if (r['source'].rstrip('/') + '/') in pages]
    assert not bad, 'these redirects shadow a page that exists: %s' % bad


def test_every_renamed_page_still_answers_on_its_old_url():
    """A rename is invisible to `mkdocs build --strict` — the old URL simply stops
    existing, in production, with nothing failing anywhere."""
    import json
    v = json.loads(_read(os.path.join(ROOT, 'vercel.json')))
    srcs = {r['source'] for r in v.get('redirects', [])}
    for old in ('/install', '/gui', '/graph', '/health', '/token-economy'):
        assert old in srcs and old + '/' in srcs, \
            '%s was renamed but has no redirect (needs both slash forms)' % old
    # An absolute destination leaves this deployment — those are the pages that
    # moved to the apex marketing site, and this host cannot check them.
    dests = {r['destination'] for r in v.get('redirects', [])
             if not r['destination'].startswith('http')}
    missing = [d for d in dests if d.strip('/') + '.md' not in _doc_pages()]
    assert not missing, 'redirects point at pages that do not exist: %s' % missing


def test_an_off_host_redirect_is_scoped_to_the_host_it_is_correct_on():
    """A redirect to an absolute URL is a LOOP on any deployment that also serves
    that URL's host, and Vercel answers the loop 308 forever rather than erroring.

    This shipped: while the docs build still answered on claudectl.space, its own
    rule `/features -> https://claudectl.space/features/` matched its own
    destination. curl -L gave up at twelve hops. `has: host` is what makes the
    rule fire only on docs.claudectl.space, where the destination is off-host and
    the redirect is the whole point."""
    import json
    v = json.loads(_read(os.path.join(ROOT, 'vercel.json')))
    bad = []
    for r in v.get('redirects', []):
        if not r['destination'].startswith('http'):
            continue
        hosts = [h.get('value') for h in r.get('has', []) if h.get('type') == 'host']
        if not hosts:
            bad.append(r['source'])
    assert not bad, (
        'these redirects leave the deployment without a host condition, so they '
        'loop wherever this build also serves the destination host: %s' % bad)


def test_each_deployment_carries_its_own_build_config():
    """The documentation is served by GitHub Pages now, so this file is no longer
    a live deployment — but it stays, and so does this gate, because it is what
    stops the marketing site inheriting a Python toolchain it cannot run.

    A Vercel project whose Root Directory has no vercel.json falls back to the one
    at the repository root. With only the root file present, the Next app was
    handed the documentation's Python install command and ran it inside www/:

        ERROR: Could not open requirements file: 'requirements-docs.txt'

    The file was committed and correct; the working directory was not the one it
    lives in. Each root directory owns its own config, and neither may carry the
    other's toolchain."""
    import json
    root = json.loads(_read(os.path.join(ROOT, 'vercel.json')))
    www = json.loads(_read(os.path.join(ROOT, 'www', 'vercel.json')))

    root_cmds = ' '.join(str(root.get(k, '')) for k in ('installCommand', 'buildCommand'))
    www_cmds = ' '.join(str(www.get(k, '')) for k in ('installCommand', 'buildCommand'))

    assert 'requirements-docs.txt' in root_cmds and 'mkdocs' in root_cmds, \
        'the repository-root config is the documentation build: %s' % root_cmds
    assert 'npm' in www_cmds, 'www/ is a Node app: %s' % www_cmds
    for leaked in ('python', 'mkdocs', 'requirements-docs.txt', '.venv'):
        assert leaked not in www_cmds, \
            'www/vercel.json carries the docs toolchain (%r): %s' % (leaked, www_cmds)

    # Both projects were once the same project, so the dashboard still carried
    # `site` as the output directory and the Next build was rejected for not
    # producing one. Each config states its own, and vercel.json wins over a
    # dashboard setting — which is the only way to stop that leftover mattering.
    assert root.get('outputDirectory') == 'site', 'the docs build emits site/'
    assert www.get('outputDirectory') == '.next', \
        'www/ must state .next, or a stale dashboard override decides: %r' % www.get('outputDirectory')


def test_nothing_opaque_is_painted_over_the_scene():
    """A block's own background is painted AFTER its negative-z-index descendants.

    The marketing site puts its WebGL canvas at `z-index: -1` and a static wash at
    `-3`, both children of <body>. `body { background: ... }` therefore covered
    both, and the landing page shipped as a flat colour: the scene drew every
    frame, the framebuffer was full, and no pixel of it reached the screen. The
    ground colour goes on <html>, whose background is the root's and paints
    first.

    Read as text rather than parsed: a CSS parser is a dependency, and the rule
    being guarded is one declaration in one block."""
    css = _read(os.path.join(ROOT, 'www', 'app', 'globals.css'))

    def block(selector):
        m = re.search(r'(?:^|\})\s*%s\s*\{([^}]*)\}' % re.escape(selector), css, re.M)
        return m.group(1) if m else ''

    assert 'background' in block('html'), \
        'the ground colour must be on <html>, which paints before the scene'
    body = block('body')
    assert body, 'no body rule found in globals.css'
    assert not re.search(r'(?<!-)\bbackground(-color)?\s*:', body), \
        'body has a background, which paints over #journey and the wash: %r' % body.strip()


def test_the_docs_toolchain_stays_out_of_the_shipped_package():
    """Zero runtime dependencies is a marketed claim. mkdocs belongs to
    requirements-docs.txt, the way ruff/build/playwright belong to their CI job."""
    pyproject = _read(os.path.join(ROOT, 'pyproject.toml'))
    assert 'mkdocs' not in pyproject.lower()
    assert not re.search(r'^dependencies\s*=', pyproject, re.M)
    assert 'mkdocs-material' in _read(os.path.join(ROOT, 'requirements-docs.txt'))


def test_the_pages_deploy_keeps_the_custom_domain():
    """GitHub Pages stores the custom domain in a CNAME file at the site root, and
    a deploy that does not carry one CLEARS the setting in the repository. So the
    domain has to live in the built output, not only in the dashboard — otherwise
    the next docs push silently takes docs.claudectl.space offline and every
    documentation link in the README, in pyproject.toml and on the apex 404s.

    MkDocs copies any non-markdown file in docs_dir into site/ verbatim, so a file
    at docs/CNAME is the whole mechanism. It must also agree with mkdocs.yml's own
    site_url, or the sitemap and the canonicals name a host the deploy does not
    claim."""
    path = os.path.join(DOCS, 'CNAME')
    assert os.path.exists(path), \
        'docs/CNAME is missing, so the next Pages deploy drops the custom domain'

    host = _read(path).strip()
    assert host == SITE_URL.split('//')[1].strip('/'), \
        'docs/CNAME (%r) and site_url (%r) name different hosts' % (host, SITE_URL)
    # A bare host, no scheme and no path: Pages rejects anything else.
    assert '/' not in host and ':' not in host, \
        'a CNAME file holds a bare hostname, not a URL: %r' % host

    assert SITE_URL in _mkdocs(), \
        'mkdocs.yml must publish the host the CNAME claims'


def test_both_hosts_assert_the_same_author_entity():
    """The apex and the docs subdomain both publish a schema.org Person with the
    same @id and the same sameAs list. That is not decoration: an unrelated Rust
    project publishes under the name claudectl, so the only thing telling a search
    engine which one this is, is one author entity corroborated by a set of
    profiles that link back.

    Two hosts asserting DIFFERENT sameAs lists is worse than one asserting none —
    it describes two people who happen to share a name. The lists live in two
    files because the two sites have no shared build, so this is the only thing
    stopping them drifting apart."""
    apex = _read(os.path.join(ROOT, 'www', 'lib', 'site.ts'))
    block = re.search(r'export const PROFILES = \[(.*?)\]', apex, re.S)
    assert block, 'www/lib/site.ts no longer exports PROFILES'
    www = set(re.findall(r"'(https?://[^']+)'", block.group(1)))

    mk = _mkdocs()
    block = re.search(r'\n  profiles:\n((?:    - \S+\n)+)', mk)
    assert block, 'mkdocs.yml no longer carries extra.profiles'
    docs = set(re.findall(r'- (\S+)', block.group(1)))

    assert www == docs, \
        'the two hosts claim different profiles: %s' % sorted(www ^ docs)
    assert www, 'the sameAs list is empty, so neither host corroborates anything'

    # The docs template must actually emit it, and point the @id at the apex —
    # one entity named from one place, not one per hostname.
    tpl = _read(os.path.join(ROOT, 'overrides', 'main.html'))
    assert 'config.extra.profiles' in tpl, \
        'overrides/main.html does not emit extra.profiles, so the docs host asserts nothing'
    assert '/#author' in tpl and 'config.extra.apex' in tpl, \
        'the docs Person @id must be the apex URL, or the two hosts are two entities'
