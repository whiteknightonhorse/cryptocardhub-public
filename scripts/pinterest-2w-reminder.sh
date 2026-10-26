#!/bin/bash
# One-shot operator reminder (CCH-PINTEREST-1019 ruling-1, operator step 3; operator asked 2026-10-19): Pinterest analytics before raising BACKFILL_PER_DAY.
cd /home/cryptocardhub && set -a && . ./.env && set +a
M=seo-site/src/data/pinterest-manifest.json
if [ -f "$M" ]; then N=$(/usr/bin/python3 -c "import json;print(len(json.load(open('$M'))))"); STATE="в ленте пинов сейчас $N статей"; else STATE="ВНИМАНИЕ: лента пинов ещё не выкачена (манифеста нет) — проверять пока нечего, перешли это диспетчеру"; fi
/usr/bin/python3 - "$STATE" <<'PY'
import sys; sys.path.insert(0,'scripts'); import telegram_lib as t
s=sys.argv[1]
msg=("📌 Pinterest DefyCard — прошло ~2 недели автопубликации (" + s + ").\n"
"Проверь и перешли диспетчеру (Claude) ответы:\n"
"1) pinterest.com → доска «Crypto Card Reviews & Comparisons»: сколько пинов, вертикальные ли, ведут ли на статью (не на главную)?\n"
"2) Pinterest → Аналитика → последние 14 дней: показы, клики по ссылке (outbound clicks), сохранения.\n"
"3) Есть ли предупреждения/ограничения от Pinterest (уведомления, письма, «спам»)?\n"
"4) Settings → Bulk create Pins → Auto-publish: нет ли ошибки у ленты https://defycard.com/pinterest.xml?\n"
"Решение после этого: поднимать ли темп старых статей с 3/день (сейчас ~190 дней на весь архив).")
print('sent', t.alert(msg))
PY
