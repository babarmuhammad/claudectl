import { FAQ, faqJsonLd } from '@/lib/faq';
import { breadcrumbs, jsonLd, meta } from '@/lib/meta';
import { SITE } from '@/lib/site';
import { Cta, PageHeader } from '@/components/Page';
import { Spine } from '@/components/site/Spine';

export const metadata = meta({
  title: 'Frequently asked questions',
  description:
    'What claudectl is, how to install it, where Claude Code keeps its sessions, how claudectl cuts token usage and handles multiple accounts — and how it differs from /resume. Answered plainly.',
  path: '/faq',
});

const CRUMBS = breadcrumbs([
  { name: 'Home', path: '/' },
  { name: 'FAQ', path: '/faq' },
]);

export default function FaqPage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(CRUMBS).text }}
      />
      {/* Generated from the same FAQ array the page renders, so the structured
          data cannot describe a question the page does not answer. */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(faqJsonLd()).text }}
      />

      <PageHeader
        eyebrow="FAQ"
        title="Frequently asked questions"
        lead={`The ${FAQ.length} questions that come up most: what claudectl does, what it costs in tokens, and how it sits alongside Claude Code's own commands.`}
      >
        <Cta href={SITE.docs} primary>
          Documentation
        </Cta>
        <Cta href="/community">Ask a question</Cta>
      </PageHeader>

      {/* Not PageBody: that caps the width at max-w-4xl, which is what left the
          old solids floating in the margin outside the column with nothing to
          sit in. The rail needs the room. */}
      <div className="py-16">
        {/* <details> is the platform's disclosure widget: keyboard and screen
            reader behaviour for free, and no client component. */}
        {/* A list, so the rows keep their natural height and the solids shrink
            to a rail on the right — padding one question per screen makes them
            worse to read, not better. */}
        <Spine
          layout="rail-right"
          items={FAQ.map(({ q, a }) => ({
            key: q,
            // A long answer is a question people ask more about; it gets the
            // bigger solid. Derived, so there is no second list of importances.
            weight: a.length,
            node: (
            <details className="panel group px-5 py-4">
              <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-[0.98rem] font-semibold leading-[1.5] text-text [&::-webkit-details-marker]:hidden">
                <span>{q}</span>
                <svg
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                  className="mt-0.5 h-4 w-4 shrink-0 text-dim2 transition-transform duration-200 group-open:rotate-180"
                >
                  <path
                    d="M6 9.5 12 15.5 18 9.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </summary>
              <p className="mt-3 max-w-3xl text-[0.93rem] leading-[1.72] text-dim">{a}</p>
            </details>
            ),
          }))}
        />
      </div>
    </>
  );
}
