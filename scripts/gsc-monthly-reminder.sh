#!/bin/bash
# Monthly operator reminder (CCH-KEYWORDS-1019 ruling-1, operator 2026-10-19): check GSC Manual actions + Security issues.
cd /home/cryptocardhub && set -a && . ./.env && set +a
/usr/bin/python3 -c "import sys; sys.path.insert(0,'scripts'); import telegram_lib as t; ok=t.alert('🔎 Раз в месяц (оператор): Google Search Console → defycard.com → «Ручные меры» и «Проблемы безопасности». Ожидается пусто. Спам-ссылки не трогать, disavow не подавать (CCH-KEYWORDS-1019). Если что-то есть — переслать диспетчеру.'); print('sent', ok)"
