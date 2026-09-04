export const SITE = {
  url: 'https://claudectl.space',
  docs: 'https://docs.claudectl.space',
  name: 'claudectl',
  tagline: 'The workspace layer for Claude Code',
  repo: 'https://github.com/babarmuhammad/claudectl',
  pypi: 'https://pypi.org/project/claudectl/',
  author: 'Babar Muhammad Anas',
  authorGithub: 'https://github.com/babarmuhammad',
  license: 'MIT',
  ogImage: '/og-card.png',
} as const;

/** Every profile the author controls, for schema.org `sameAs`.
 *
 *  This is the disambiguation mechanism, not decoration: an unrelated Rust
 *  project publishes under the same name, so the thing that tells a search
 *  engine which "claudectl" this is, is one entity corroborated by a set of
 *  profiles that all link back here. Add a URL only once it exists AND links
 *  to claudectl.space — a dead or one-way profile weakens the graph.
 *
 *  Deliberately absent: the Reddit account. It is pseudonymous and shares no
 *  string with the name or the project, so it corroborates nothing, and
 *  publishing it here would permanently tie that pseudonym to a real identity.
 *  It lives in the growth repo's ledger, which is where it is actually used. */
export const PROFILES = [
  'https://github.com/babarmuhammad',
  'https://www.linkedin.com/in/muhammad-anas-babar-819647240',
  'https://dev.to/muhammad_anasbabar_31256',
  'https://babarmuhammad.hashnode.dev',
  'https://www.instagram.com/muhammad_anas_babar',
] as const;

export const NAV = [
  { href: '/features', label: 'Features' },
  { href: '/download', label: 'Download' },
  { href: '/architecture', label: 'Architecture' },
  { href: '/changelog', label: 'Changelog' },
  { href: '/blog', label: 'Blog' },
  { href: '/faq', label: 'FAQ' },
  { href: '/community', label: 'Community' },
  { href: '/about', label: 'About' },
] as const;

/** Old docs URLs that now live on the docs subdomain. */
export const DOC_REDIRECTS = [
  'install', 'usage', 'gui', 'graph', 'token-economy', 'health', 'sessions',
  'memory', 'accounts', 'mcp', 'agents', 'hooks', 'statusline', 'plan-execute',
  'reference', 'api', 'dashboard', 'context-handoff', 'agent-library',
] as const;

export const url = (path: string) => new URL(path, SITE.url).toString();
