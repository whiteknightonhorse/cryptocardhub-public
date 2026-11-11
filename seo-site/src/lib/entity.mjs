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

/**
 * CCH-SEO-34. The editor is a Person who owns the policy; he is never the author
 * (pages are drafted by a model). Only name/role from entity.json; sameAs only if given.
 */
export function editorSchema(entity, siteOrigin) {
  const e = entity && entity.editor;
  if (!e || !e.name) return null;
  const p = {
    '@type': 'Person',
    '@id': `${siteOrigin}/editorial-policy/#editor`,
    name: e.name,
    jobTitle: e.role,
    worksFor: { '@id': `${siteOrigin}/#org` },
  };
  if (e.sameAs && e.sameAs.length) p.sameAs = e.sameAs;
  return p;
}
