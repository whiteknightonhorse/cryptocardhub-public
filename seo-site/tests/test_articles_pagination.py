"""G35 — /articles/ постранично, и ни одна статья не пропала из объединения страниц.

Зачем гейт. Индекс рисовал ВСЕ статьи одним документом: живая страница 2026-08-18 отдавала
323 863 байта и 555 ссылок, и росла с каждой публикацией — по ~12 статей в неделю от одних
только FAQ-детей. Ровно тот же дефект, что закрыли на /videos/ (206 карточек, 150 КБ), и
закрыт он тем же приёмом.

Пагинацию легко сломать так, что первая страница выглядит правильно: одни и те же двадцать
карточек на каждой странице, или страницы 2..N построены пустыми. Оба отказа невидимы, пока
никто не считает ОБЪЕДИНЕНИЕ.

Читается СОБРАННЫЙ dist, а не исходник: значение имеет то, что получает обходчик. Ожидания —
литералы.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "articles"
CONTENT = ROOT / "src" / "content" / "pseo"

PAGE_SIZE = 20  # обязан совпадать с PAGE_SIZE в src/pages/articles/[...page].astro


def _html(rel: str) -> str:
    p = DIST / rel
    if not p.exists():
        pytest.fail(f"{p} нет — сначала собери: cd seo-site && pnpm build")
    return p.read_text(encoding="utf-8")


def _article_links(html: str) -> list[str]:
    """Ссылки на статьи в порядке документа, без повторов."""
    out: list[str] = []
    body = html.split('<nav class="articles-pager"', 1)[0]  # пагинатор — не карточки
    for m in re.finditer(r'href="(/(?:hub|compare|reviews|geo|tools|how-to|for|learn|help|news|faq)/[a-z0-9-]+/)"', body):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _page_files() -> list[Path]:
    pages = [DIST / "index.html"]
    pages += sorted(
        (d / "index.html" for d in DIST.iterdir() if d.is_dir() and d.name.isdigit()),
        key=lambda p: int(p.parent.name),
    )
    return [p for p in pages if p.exists()]


@pytest.mark.parametrize("rel", ["index.html", "2/index.html"])
def test_the_first_pages_exist(rel: str) -> None:
    """Первая страница обязана остаться по адресу /articles/ — иначе ломаются внешние ссылки."""
    assert _html(rel)


def test_no_page_exceeds_the_declared_size() -> None:
    for p in _page_files():
        n = len(_article_links(p.read_text(encoding="utf-8")))
        assert n <= PAGE_SIZE, f"{p} несёт {n} статей при потолке {PAGE_SIZE}"


def test_pages_are_distinct_not_the_same_twenty_repeated() -> None:
    """Самый коварный отказ: пагинация есть, а срез один и тот же."""
    seen: dict[str, str] = {}
    for p in _page_files():
        for link in _article_links(p.read_text(encoding="utf-8")):
            prev = seen.get(link)
            assert prev is None, f"{link} встречается и в {prev}, и в {p}"
            seen[link] = str(p)
    assert len(seen) > PAGE_SIZE, "объединение не больше одной страницы — пагинация фиктивна"


def test_every_article_stays_reachable() -> None:
    """Объединение страниц покрывает ВСЮ коллекцию: пагинация не смеет терять статьи."""
    union = set()
    for p in _page_files():
        union |= set(_article_links(p.read_text(encoding="utf-8")))
    total = len(list(CONTENT.glob("*.md")))
    assert total > 0, "коллекция pseo пуста — тест нечем судить"
    assert len(union) == total, (
        f"на страницах {len(union)} статей, в коллекции {total} — "
        f"пагинация потеряла {total - len(union)}")


def test_the_pager_carries_prev_next_and_marks_the_current_page() -> None:
    """Разметка перелистывания — это то, по чему обходчик проходит корпус вглубь."""
    second = _html("2/index.html")
    assert 'rel="prev"' in second and 'rel="next"' in second, "нет rel prev/next на второй странице"
    assert 'aria-current="page"' in second, "текущая страница не помечена"
    first = _html("index.html")
    assert 'rel="prev"' not in first, "у первой страницы не должно быть «предыдущей»"
