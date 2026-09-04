import type { Metadata, Viewport } from 'next';
import './globals.css';
import { Header } from '@/components/site/Header';
import { Footer } from '@/components/site/Footer';
import { Backdrop } from '@/components/site/Backdrop';
import { RouteFade } from '@/components/site/RouteFade';
import { PROFILES, SITE, url } from '@/lib/site';
import { HOME } from '@/lib/content';
import { jsonLd } from '@/lib/meta';
import { VERSION } from '@/lib/build-data';

/* No next/font: the type stack in globals.css is fonts that ship with the OS,
   exactly as the app itself does. A webfont here would be a build-time network
   call and a render-blocking request for a look we already have. */

export const metadata: Metadata = {
  metadataBase: new URL(SITE.url),
  title: {
    default: HOME.title,
    template: `%s · ${SITE.name}`,
  },
  description: HOME.description,
  applicationName: SITE.name,
  authors: [{ name: SITE.author, url: SITE.authorGithub }],
  creator: SITE.author,
  keywords: [
    'Claude Code',
    'Claude Code session manager',
    'Claude Code memory',
    'CLAUDE.md',
    'MCP server manager',
    'Claude Code token usage',
    'AI coding workspace',
    'developer tools',
  ],
  openGraph: {
    type: 'website',
    url: SITE.url,
    siteName: SITE.name,
    title: HOME.title,
    description: HOME.description,
    images: [{ url: url(SITE.ogImage), width: 1200, height: 630, alt: SITE.tagline }],
  },
  twitter: {
    card: 'summary_large_image',
    title: HOME.title,
    description: HOME.description,
    images: [url(SITE.ogImage)],
  },
  robots: { index: true, follow: true },
  icons: { icon: '/favicon.ico' },
  // Feed discovery. A reader (and several crawlers) look for this link before
  // they look for anything else on the page.
  alternates: {
    canonical: SITE.url,
    types: { 'application/rss+xml': [{ url: url('/blog/rss.xml'), title: `${SITE.name} blog` }] },
  },
};

export const viewport: Viewport = {
  themeColor: '#0a0c10',
  colorScheme: 'dark',
};

/** The site-wide entity graph, in the layout so every page carries it.
 *  Page-specific graphs (FAQPage, BlogPosting) are added per route.
 *
 *  Three nodes rather than one bare SoftwareApplication, because an unrelated
 *  Rust project ships under this name: the Person is the hub every profile in
 *  PROFILES points back to, and `sameAs` is how a search engine merges those
 *  scattered profiles into one entity instead of two projects called claudectl.
 *  `alternateName` gives it the strings people actually type. */
const AUTHOR_ID = `${SITE.url}/#author`;

const SITE_LD = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'Person',
      '@id': AUTHOR_ID,
      name: SITE.author,
      url: SITE.url,
      sameAs: [...PROFILES],
    },
    {
      '@type': 'WebSite',
      '@id': `${SITE.url}/#website`,
      name: SITE.name,
      alternateName: ['claudectl (Python)', 'claudectl for Claude Code'],
      url: SITE.url,
      description: HOME.description,
      inLanguage: 'en',
      publisher: { '@id': AUTHOR_ID },
    },
    {
      '@type': 'SoftwareApplication',
      '@id': `${SITE.url}/#software`,
      name: SITE.name,
      alternateName: ['claudectl (Python)', 'claudectl for Claude Code'],
      identifier: 'claudectl',
      applicationCategory: 'DeveloperApplication',
      operatingSystem: 'Windows, macOS, Linux',
      description: HOME.description,
      url: SITE.url,
      downloadUrl: SITE.pypi,
      installUrl: SITE.pypi,
      ...(VERSION ? { softwareVersion: VERSION } : {}),
      license: 'https://opensource.org/licenses/MIT',
      isAccessibleForFree: true,
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
      author: { '@id': AUTHOR_ID },
      maintainer: { '@id': AUTHOR_ID },
      codeRepository: SITE.repo,
      programmingLanguage: 'Python',
      sameAs: [SITE.repo, SITE.pypi, SITE.docs],
    },
  ],
};

export default function RootLayout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className="h-full">
      <body className="flex min-h-full flex-col antialiased">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: jsonLd(SITE_LD).text }}
        />
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-panel2 focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        <Backdrop />
        <Header />
        <main id="main" className="flex-1">
          <RouteFade>{children}</RouteFade>
        </main>
        <Footer />
      </body>
    </html>
  );
}
