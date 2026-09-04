# claudectl.space

The marketing site for [claudectl](https://github.com/babarmuhammad/claudectl), the
workspace layer for Claude Code. Next.js App Router, deployed to Vercel from `main`.

The documentation is a separate site: MkDocs Material in `../docs`, served at
[docs.claudectl.space](https://docs.claudectl.space/). Each root directory owns its own
`vercel.json`, because a project whose root has none falls back to the repository's and
gets handed the wrong build command.

## Running it

```bash
npm ci
npm run dev     # http://localhost:3000
npm run build   # what CI and Vercel run
npm run lint
```

Node 20 or newer. The `www` job in `.github/workflows/ci.yml` runs lint and build on every
push, so a break shows up on the pull request rather than only in the deploy.

## Where the content lives

Nothing on this site is written twice.

`lib/site.ts` holds the site identity: URLs, tagline, author, and `PROFILES`, the
schema.org `sameAs` list. That list has to stay in step with `extra.profiles` in
`../mkdocs.yml` — both hosts assert the same author entity, and
`tests/test_docs_site.py::test_both_hosts_assert_the_same_author_entity` fails when they
drift apart.

`lib/content.ts` holds the page copy as structured `Doc` objects, so a page and the
sitemap and `llms.txt` all read the same source. `lib/faq.ts` is one array that renders as
the FAQ page and as its `FAQPage` JSON-LD. `content/blog/*.md` is markdown with front
matter, parsed by `lib/blog.ts`. The changelog and the About page's numbers are read out
of the repository at build time by `lib/build-data.ts`, which fails open when a file is
missing so a checkout of `www` alone still builds.

`lib/meta.ts` is the only place that knows the canonical, OpenGraph and Twitter shape a
route needs. Call `meta()` from a page rather than writing a `metadata` export by hand.

## Generated routes

`app/sitemap.ts`, `app/robots.ts`, `app/llms.txt`, `app/llms-full.txt` and
`app/blog/rss.xml` all derive their contents from the tables above. None of them holds a
list of pages, because a second hand-maintained list is a list that falls behind.

Keep them deterministic: no wall clock, no `Date.now()`. Two builds of one commit should
produce identical bytes, which is why the apex sitemap carries no `lastmod` and the feed
has no `lastBuildDate`.

## The background

`components/journey` is a scroll-driven three.js scene. Every word of copy is
server-rendered DOM positioned over it, so the page reads the same with WebGL disabled,
in a crawler, and in `curl`. If you add a section, add the copy to `lib/content.ts` first
and let the scene follow.
