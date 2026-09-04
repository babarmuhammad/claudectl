import type { MetadataRoute } from 'next';
import { HOME } from '@/lib/content';
import { SITE } from '@/lib/site';

/** A web manifest is not here to make the site installable — it is a second
 *  machine-readable statement of the same identity the JSON-LD makes, which is
 *  worth having when an unrelated project publishes under this name. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: `${SITE.name} — ${SITE.tagline}`,
    short_name: SITE.name,
    description: HOME.description,
    start_url: '/',
    display: 'browser',
    background_color: '#0a0c10',
    theme_color: '#0a0c10',
    icons: [{ src: '/favicon.ico', sizes: '256x256', type: 'image/x-icon' }],
  };
}
