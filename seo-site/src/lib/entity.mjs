/**
 * entity.mjs — CCH-SEO-10. Who we say we are (sameAs) and who wrote it (author).
 * Data lives in src/data/entity.json; filling `author` turns the byline on.
 * No person is ever invented here: author null = the Organization stays the author.
 */
export function authorSchema(entity, fallbackOrg) {
  const a = entity && entity.author;
  if (!a || !a.name) return fallbackOrg;
  const p = { '@type': 'Person', name: a.name };
  if (a.url) p.url = a.url;
  if (a.sameAs && a.sameAs.length) p.sameAs = a.sameAs;
  return p;
}
