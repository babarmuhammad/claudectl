import type { MetadataRoute } from 'next';
import { allPosts } from '@/lib/blog';
import { DOCS } from '@/lib/content';
import { NAV, url } from '@/lib/site';

/**
 * The route list is derived, not written down again. NAV and DOCS already know
 * every apex page between them, and a second hand-maintained copy is a copy that
 * will be a page behind the next time one is added.
 *
 * /code-of-conduct is the one route neither table names — it is rendered from the
 * repository's CODE_OF_CONDUCT.md and appears only in the footer.
 */
const APEX = [
  ...new Set([
    '/code-of-conduct',
    ...DOCS.map((d) => `/${d.slug}`), // HOME's slug is '', so this yields '/'
    ...NAV.map((n) => n.href as string),
  ]),
];

/** Pages that change on a release, versus pages that change when I rewrite them. */
const OFTEN = new Set(['/', '/changelog', '/blog', '/download']);

/* No lastModified on the apex entries, deliberately. There is no honest value
   for one: these pages are rendered from lib/content.ts, so git gives one date
   for a dozen URLs, and the deploy commit's date would claim every page changed
   on every push. Google discounts a lastmod it finds unreliable and says to omit
   it rather than guess, so the blog — which has a real per-post date — is the
   only place it appears. */

const priority = (path: string) =>
  path === '/' ? 1 : path === '/features' || path === '/download' ? 0.9 : path === '/blog' ? 0.8 : 0.6;

export default function sitemap(): MetadataRoute.Sitemap {
  const posts = allPosts();

  return [
    ...APEX.map((path) => ({
      url: url(path),
      changeFrequency: (OFTEN.has(path) ? 'weekly' : 'monthly') as 'weekly' | 'monthly',
      priority: priority(path),
    })),
    ...posts.map((post) => ({
      url: url(`/blog/${post.slug}`),
      // No wall clock: the post's own date is the only honest lastModified, and
      // it keeps the sitemap byte-identical between two builds of one commit.
      lastModified: post.date,
      changeFrequency: 'yearly' as const,
      priority: 0.7,
    })),
  ];
}
