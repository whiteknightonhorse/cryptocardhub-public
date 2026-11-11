/**
 * videos.mjs — heroVideoFor: the one implementation of the hero law.
 * The law is stated in the header of pages/videos/[id].astro. `videos` are
 * collection `.data` objects; the caller guarantees the article exists.
 */
export function heroVideoFor(targetSlug, videos) {
  return videos
    .filter((v) => v.target_pseo_slug === targetSlug)
    .sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at) || (a.id < b.id ? -1 : 1))[0] || null;
}
