// /rss.xml — 50 newest articles by BIRTH date (schema_article.datePublished),
// never generated_at (that moves on every edit). Absolute links via `site`.
import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';

export async function GET(context) {
  const all = await getCollection('pseo');
  const items = all
    .map((e) => ({ e, born: e.data.schema_article?.datePublished }))
    .filter((x) => x.born && !Number.isNaN(Date.parse(x.born)))
    .sort((a, b) => Date.parse(b.born) - Date.parse(a.born))
    .slice(0, 50)
    .map(({ e, born }) => ({
      title: e.data.title,
      link: `/${e.data.bucket}/${e.data.slug}/`,
      pubDate: new Date(born),
      description: e.data.description,
    }));
  return rss({
    title: 'DefyCard',
    description: 'Crypto card reviews, comparisons and guides.',
    site: context.site,
    items,
  });
}
