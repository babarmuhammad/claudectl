import { DOWNLOAD } from '@/lib/content';
import { VERSION } from '@/lib/build-data';
import { breadcrumbs, jsonLd, meta } from '@/lib/meta';
import { SITE, url } from '@/lib/site';
import { Cta, DocSections, PageHeader } from '@/components/Page';
import { CopyButton } from '@/components/CopyButton';

export const metadata = meta({
  title: DOWNLOAD.title,
  description: DOWNLOAD.description,
  path: '/download',
});

const CRUMBS = breadcrumbs([
  { name: 'Home', path: '/' },
  { name: 'Download', path: '/download' },
]);

/** The page-level application graph. The layout already carries one for the
 *  site; this is the one a crawler sees on the page that actually installs, so
 *  it names the version and the requirement.
 *
 *  Same `@id` as the layout's node on purpose — without it these are two
 *  SoftwareApplications with the same name on one page, which is the ambiguity
 *  the entity graph exists to remove. With it they merge, and this page just
 *  adds `softwareRequirements`. */
const APP_LD = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  '@id': `${SITE.url}/#software`,
  name: SITE.name,
  applicationCategory: 'DeveloperApplication',
  operatingSystem: 'Windows, macOS, Linux',
  description: DOWNLOAD.description,
  url: url('/download'),
  downloadUrl: SITE.pypi,
  installUrl: SITE.pypi,
  ...(VERSION ? { softwareVersion: VERSION } : {}),
  softwareRequirements: 'Python 3.10 or newer',
  license: 'https://opensource.org/licenses/MIT',
  isAccessibleForFree: true,
  offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
  author: { '@id': `${SITE.url}/#author` },
  codeRepository: SITE.repo,
  programmingLanguage: 'Python',
};

/** The commands come out of the Doc, so the cards at the top and the sections
 *  below them cannot disagree about what to type. */
function install(id: string) {
  const section = DOWNLOAD.sections.find((s) => s.id === id);
  const block = section?.blocks.find((b) => b.kind === 'code');
  return {
    label: section?.heading ?? id,
    command: block?.kind === 'code' ? block.text : '',
  };
}

const CARDS = ['pipx', 'pip', 'plugin'].map((id) => ({ id, ...install(id) }));

/* The ids belong to lib/content.ts, which this route does not own. Renaming one
   there would otherwise ship an install card with an empty command in it — the
   single worst thing this page can do — so it fails the build instead. */
const MISSING = CARDS.filter((c) => !c.command).map((c) => c.id);
if (MISSING.length) {
  throw new Error(`/download: no install command in lib/content.ts for: ${MISSING.join(', ')}`);
}

function CommandCard({ label, command }: { label: string; command: string }) {
  return (
    <div className="panel-solid flex min-w-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2">
        <span className="truncate font-mono text-[0.7rem] uppercase tracking-[0.14em] text-dim2">
          {label}
        </span>
        <CopyButton text={command} />
      </div>
      {/* Wrapped, not scrolled. A command longer than the card — the plugin one
          is — ends mid-word against the border with no scrollbar to suggest
          there is more, and this is the one block on the site a visitor is
          meant to read in full before copying it. */}
      <pre className="whitespace-pre-wrap break-all px-4 py-3 font-mono text-[0.82rem] leading-[1.75] text-module">
        <code>{command}</code>
      </pre>
    </div>
  );
}

export default function DownloadPage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(CRUMBS).text }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(APP_LD).text }}
      />

      <PageHeader
        eyebrow={VERSION ? `Download · v${VERSION}` : 'Download'}
        title={DOWNLOAD.h1 ?? DOWNLOAD.title}
        lead={DOWNLOAD.intro}
      >
        <Cta href={SITE.pypi} primary>
          claudectl on PyPI
        </Cta>
        <Cta href={`${SITE.repo}/releases`}>GitHub releases</Cta>
        <Cta href={`${SITE.docs}/installation/`}>Installation guide</Cta>
      </PageHeader>

      <div className="mx-auto max-w-4xl px-5 pt-10">
        <div className="grid gap-4 md:grid-cols-3">
          {CARDS.map((c) => (
            <CommandCard key={c.id} label={c.label} command={c.command} />
          ))}
        </div>
        <p className="mt-4 text-[0.85rem] text-dim2">
          Python 3.10 or newer, no runtime dependencies, and no API key — claudectl uses the
          Claude Code authentication you already have. Every tagged build is also on the{' '}
          <a
            href={`${SITE.repo}/releases`}
            className="text-cyan no-underline underline-offset-[3px] hover:underline"
          >
            releases page
          </a>
          .
        </p>
      </div>

      {/* A sequence, so the solids stay in one column and read as rungs. */}
      <DocSections doc={DOWNLOAD} layout="ladder" />
    </>
  );
}
