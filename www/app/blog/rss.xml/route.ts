import { allPosts } from '@/lib/blog';
import { SITE, url } from '@/lib/site';

/**
 * /blog/rss.xml — how aggregators, readers and several AI crawlers find a new
 * post without waiting to be crawled. Built from the same `allPosts()` the blog
 * pages use, so a feed entry cannot describe a post the site does not have.
 *
 * RSS 2.0 rather than Atom: it is what every reader accepts, and `pubDate`
 * wants RFC 822, which is the only conversion here.
 */
export const dynamic = 'force-static';

/** XML has five, but a text node only ever needs these three. */
const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** yyyy-mm-dd -> RFC 822. Anchored to UTC noon so a reader in any timezone
 *  still shows the day the post is dated, not the one before it. */
const rfc822 = (iso: string) => new Date(`${iso}T12:00:00Z`).toUTCString();

export function GET() {
  const posts = allPosts();
  const self = url('/blog/rss.xml');

  const items = posts.map((p) => {
    const link = url(`/blog/${p.slug}`);
    return [
      '    <item>',
      `      <title>${esc(p.title)}</title>`,
      `      <link>${link}</link>`,
      `      <guid isPermaLink="true">${link}</guid>`,
      `      <pubDate>${rfc822(p.date)}</pubDate>`,
      `      <description>${esc(p.description)}</description>`,
      ...p.tags.map((t) => `      <category>${esc(t)}</category>`),
      '    </item>',
    ].join('\n');
  });

  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    '  <channel>',
    `    <title>${esc(SITE.name)} blog</title>`,
    `    <link>${url('/blog')}</link>`,
    `    <atom:link href="${self}" rel="self" type="application/rss+xml" />`,
    `    <description>${esc(`Long-form notes on working with Claude Code, from the author of ${SITE.name}.`)}</description>`,
    '    <language>en</language>',
    // No lastBuildDate: a wall clock would make two builds of one commit differ.
    ...items,
    '  </channel>',
    '</rss>',
    '',
  ].join('\n');

  return new Response(body, {
    headers: { 'content-type': 'application/rss+xml; charset=utf-8' },
  });
}
