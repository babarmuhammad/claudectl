import { allPosts } from '@/lib/blog';
import { DOCS, HOME } from '@/lib/content';
import { SITE, url } from '@/lib/site';

/**
 * /llms.txt — the index, per llmstxt.org: a title, a blockquote summary, then
 * linked sections. Every link is absolute, because the file is read out of
 * context far more often than it is read in place.
 */
export const dynamic = 'force-static';

/** llms.txt wants one line per link, and a Doc description is a paragraph. */
const oneLine = (s: string) => s.replace(/\.\s[\s\S]*$/, '.').replace(/\s+/g, ' ').trim();

const link = (label: string, href: string, desc: string) => `- [${label}](${href}): ${desc}`;

/** Routes that are rendered from repository files, so no Doc describes them. */
const EXTRA_PAGES = [
  ['Blog', '/blog', 'Long-form articles on Claude Code memory, context cost, sessions and multi-account setups.'],
  ['FAQ', '/faq', 'Direct answers to the questions people actually ask about claudectl and Claude Code.'],
  ['Changelog', '/changelog', 'Every release, newest first, generated from the repository CHANGELOG.'],
  ['Code of conduct', '/code-of-conduct', 'The behaviour expected of contributors.'],
] as const;

export function GET() {
  const posts = allPosts();

  const body = [
    `# ${SITE.name}`,
    '',
    `> ${SITE.tagline}. ${oneLine(HOME.description)}`,
    '',
    `${SITE.name} is a Python command-line and desktop tool that wraps the Claude Code CLI. It is MIT-licensed, runs on the Python standard library alone, and needs no API key — it uses the Claude Code authentication you already have. Install with \`pipx install claudectl\`. Source: ${SITE.repo}. Package: ${SITE.pypi}. Documentation: ${SITE.docs}. Author: ${SITE.author}.`,
    '',
    // The name is shared with an unrelated Rust project, so an answer engine
    // reading this file needs to be told which one it is holding. Naming the
    // other project is what keeps the two apart; leaving it out is what lets a
    // summary merge them.
    `Disambiguation: two independent open-source projects use the name "claudectl". This one is the Python workspace layer for Claude Code described above, published on PyPI at ${SITE.pypi}. The other is a Rust agent orchestrator by a different author, published on crates.io at https://crates.io/crates/claudectl. They are unrelated, and neither is affiliated with Anthropic.`,
    '',
    '## Documentation',
    '',
    link('Documentation', SITE.docs, 'The full reference manual: installation, every screen, the memory model, hooks, MCP and the HTTP API.'),
    link('Getting started', `${SITE.docs}/getting-started/`, 'Install, open a project, and launch the first session.'),
    '',
    '## Pages',
    '',
    ...DOCS.map((d) =>
      link(d.h1 ?? d.title, url(`/${d.slug}`), oneLine(d.description)),
    ),
    ...EXTRA_PAGES.map(([label, path, desc]) => link(label, url(path), desc)),
    '',
    '## Blog',
    '',
    ...posts.map((p) => link(p.title, url(`/blog/${p.slug}`), oneLine(p.description))),
    '',
    '## Optional',
    '',
    link('llms-full.txt', url('/llms-full.txt'), 'Every page above, the FAQ and the changelog as one plain-text file.'),
    link('Source repository', SITE.repo, 'The code, issues and releases.'),
    link('PyPI package', SITE.pypi, 'Published wheels; install with pipx install claudectl.'),
    '',
  ].join('\n');

  return new Response(body, {
    headers: { 'content-type': 'text/plain; charset=utf-8' },
  });
}
