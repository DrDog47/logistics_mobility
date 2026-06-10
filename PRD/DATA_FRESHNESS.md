# Календарь актуализации данных

Этот документ — карта всех данных в системе, которые "не вечны". У каждого пункта есть источник, периодичность обновления, что сломается если устареет, и куда подписаться, чтобы не пропустить.

> **Главное правило**: расчёты прошлых периодов сохраняют исторические значения через `CountryRateSnapshot` и копии YAML в git. Поэтому обновление YAML на сегодня **не ломает** прошлые периоды. Но если ставка реально изменилась 1 января, а вы обновили YAML только в марте — расчёты за январь-февраль будут с устаревшим значением.

---

## TL;DR — что и когда

| # | Данные | Файл | Частота | Следующая проверка | Источник |
|---|--------|------|---------|--------------------|----------|
| 1 | Прогноз. ср. зарплата PL | `tax_rules/pl_YYYY.yaml` | ежегодно | Ноябрь 2026 (на 2027) | Monitor Polski |
| 2 | Мин. зарплата PL | `tax_rules/pl_YYYY.yaml` | ежегодно (иногда +1 в июле) | Сентябрь 2026 | Dziennik Ustaw |
| 3 | ZUS rates PL | `tax_rules/pl_YYYY.yaml` | редко (политич. решения) | Раз в 5 лет | sejm.gov.pl + zus.pl |
| 4 | PIT brackets PL | `tax_rules/pl_YYYY.yaml` | редко | Раз в 3-5 лет | Ustawa o PIT |
| 5 | Виртуальные диеты 60/20 EUR | `tax_rules/pl_YYYY.yaml` | редко (изменение ustawy) | Постоянный мониторинг | Sejm |
| 6 | Sanitariaty 60 PLN | `tax_rules/pl_YYYY.yaml` | редко | Постоянный мониторинг | Ustawa o czasie pracy kierowców |
| 7 | DE Mindestlohn | `country_rates/de.yaml` | ~раз в 2 года | 1 января 2027 (14.60 EUR уже заложен) | mindestlohnkommission.de |
| 8 | FR SMIC | `country_rates/fr.yaml` | ежегодно (+ при инфляции) | 1 января 2027 | legifrance.gouv.fr |
| 9 | FR CCNTR коэффициенты | `country_rates/fr.yaml` | ежегодно (NAO) | Весна 2027 | franceroutes.fr |
| 10 | IT CCNL Logistica | `country_rates/it.yaml` | по соглашениям | 1 января 2027 (заложен) | contrattotrasporti.it |
| 11 | EUR/PLN курс | БД `nbp_rates` | ежедневно (рабочие дни) | автозагрузка | api.nbp.pl |
| 12 | Контракты водителей | БД `driver_contracts` | по факту | вручную при истечении | внутреннее |
| 13 | Документы водителей | (Phase 8) | по факту | — | — |

Команда для проверки всего разом:
```bash
docker compose exec app flask rates verify     # страновые ставки
docker compose exec app flask tax verify       # польские параметры (добавлено в Phase 2)
```

---

## Календарь по месяцам

Ниже — что и когда смотреть в течение года. Цикл рассчитан на нормативную регулярность Польши и ЕС.

### Январь
- **Старт нового года** — убедиться что `pl_YYYY.yaml` за наступивший год существует и используется
- **DE Mindestlohn** — проверить что новое значение Mindestlohn вступило в силу (если был запланирован шаг)
- **FR SMIC** — обновляется автоматически 1 января, проверить новое значение
- **IT CCNL** — если CCNL содержит увеличение на эту дату, обновить

### Февраль-Март
- **Проверка финальных значений за 2025** в Monitor Polski (прогноз → факт)
- **Аудит расчётов за январь** — выборочно проверить 1-2 периода вручную против калькуляторов в интернете

### Апрель-Июнь
- **FR NAO** — обычно в марте-апреле подписываются avenant'ы CCNTR, ставки могут поменяться задним числом или с даты подписания

### Июль
- **Возможный midyear update мин. зарплаты PL** — если инфляция превысила прогноз. Раньше было ритуалом 2 раза в год; в 2025-2026 отменили, но может вернуться

### Август-Сентябрь
- **Rozporządzenie Rady Ministrów** о мин. зарплате на следующий год — публикация в Dzienniku Ustaw примерно 15 сентября
- Скачать новое значение, начать черновик `pl_NEXT_YYYY.yaml`

### Октябрь-Ноябрь
- **Obwieszczenie Ministra Rodziny** о прогнозируемой средней зарплате — публикация в Monitorze Polskim в ноябре (для 2026 года было 19.11.2025)
- Обновить `average_wage_pln_monthly` в `pl_NEXT_YYYY.yaml`
- Обновить `zus_annual_cap_pln` = 30 × новая средняя
- Обновить `virtual_diet.zus.threshold` и `floor` (по закону = текущая средняя)

### Декабрь
- **Финализировать `pl_NEXT_YYYY.yaml`** — все параметры на следующий год должны быть в git
- **FR SMIC** на след. год — декрет публикуется в декабре
- **DE Mindestlohn** — если есть решение Mindestlohnkommission на след. год
- **IT CCNL** — следующая транша по графику обновлений
- **Полный аудит** годовых расчётов перед формированием PIT-11/PIT-4R (это уже Phase 7)

---

## A. Польские параметры (`data/tax_rules/pl_YYYY.yaml`)

### A.1 Прогнозируемое среднее вознаграждение
- **Где**: `average_wage_pln_monthly`
- **Текущее**: 9 420 PLN/мес (2026), 8 673 PLN/мес (2025)
- **Когда обновляется**: ежегодно, обвieshczenie Министра Rodziny, Pracy i Polityki Społecznej, публикуется в Monitorze Polskim в ноябре (для 2026 года было 19.11.2025, M.P. 2025 poz. 1206)
- **Источник**:
  - https://www.zus.pl/baza-wiedzy/skladki-wskazniki-odsetki/wskazniki/minimalne-i-przecietne-wynagrodzenie
  - https://monitorpolski.gov.pl/
- **Импакт**: КРИТИЧНЫЙ. Определяет:
  - Порог применения виртуальной диеты ZUS (если месячный gross > average → применяется)
  - Floor для ZUS base (база не может упасть ниже среднего после применения диеты)
  - 30-кратный годовой cap для ZUS (30 × average)
- **Зависимости** при обновлении (изменяя одно — изменить все три):
  - `average_wage_pln_monthly` → главное число
  - `international_driver.zus.threshold_monthly_pln` = тому же значению
  - `international_driver.zus.floor_pln` = тому же значению
  - `zus_annual_cap_pln` = 30 × это значение
- **Действие**:
  ```bash
  cp data/tax_rules/pl_2026.yaml data/tax_rules/pl_2027.yaml
  # отредактировать все 4 поля выше
  # обновить блок verified.at
  git add data/tax_rules/pl_2027.yaml
  git commit -m "tax: add 2027 params per M.P. 2026 poz. XXXX"
  docker compose restart app
  ```

### A.2 Минимальная зарплата
- **Где**: `minimum_wage_pln_monthly`
- **Текущее**: 4 806 PLN/мес (2026), 4 666 PLN/мес (2025)
- **Когда обновляется**: ежегодно, Rozporządzenie Rady Ministrów, публикуется в Dzienniku Ustaw в сентябре (для 2026 — 11.09.2025, Dz.U. 2025 poz. 1242)
- **Источник**:
  - https://isap.sejm.gov.pl/ (поиск "minimalne wynagrodzenie")
  - https://dziennikustaw.gov.pl/
- **Импакт**: УМЕРЕННЫЙ (для Phase 2). В калькуляторе umowa o pracę сейчас не используется напрямую, но влияет на:
  - PPK (когда добавим в Phase 6)
  - Расчёт ekwiwalent za urlop (Phase 6)
  - Минимум вынагrodzenia chorobowego (Phase 6)
  - Минимум подосновы składki zdrowotnej для предпринимателей (если добавим B2B в Phase 5)
- **Действие**: обновить `minimum_wage_pln_monthly` в новом `pl_YYYY.yaml`

### A.3 ZUS rates (стопы складок)
- **Где**: `zus_employee.{emerytalne_pct, rentowe_pct, chorobowe_pct}`
- **Текущее**: 9.76 / 1.50 / 2.45 (= 13.71% итого, работник)
- **Когда обновляется**: РЕДКО. Стопы не менялись с 2022 года. Изменение требует ustawy.
- **Импакт**: КРИТИЧНЫЙ при изменении — пересчитывает всю ZUS-часть расчёта.
- **Действие**: следить за политическими дискуссиями про ZUS reform; обычно объявляют за 6+ месяцев. При изменении — обновить YAML на год вступления в силу.

### A.4 PIT — ставки и брекеты
- **Где**: `pit.{bracket_1_rate_pct, bracket_2_rate_pct, bracket_1_threshold_pln_annual}`
- **Текущее**: 12% / 32% / 120 000 PLN annual
- **Когда обновляется**: РЕДКО. С 2022 действует ustawa "Polski Ład 2.0" (бывшая Polski Ład). Политически вульнерабельна — каждые выборы обсуждают.
- **Импакт**: КРИТИЧНЫЙ при изменении. Особенно второй брекет 32% — Phase 6 будет реализовывать кумулятивный годовой учёт.
- **Действие**: следить за обсуждениями в Sejm, новости INFOR, Rzeczpospolita

### A.5 PIT — kwota wolna (free amount)
- **Где**: `pit.free_amount_pln_annual` = 30 000, и `monthly_tax_reduction_pln` = 300
- **Текущее**: 30 000 PLN/год → 300 PLN/мес уменьшения налога
- **История**: было 8 000 до 2022, потом стало 30 000 после Polski Ład
- **Когда обновляется**: при изменении ustawy o PIT
- **Зависимости**: `monthly_tax_reduction_pln` = `free_amount_pln_annual × bracket_1_rate_pct / 100 / 12` (= 30000 × 12% / 12 = 300)
- **Действие**: при изменении пересчитать обе константы

### A.6 PIT — koszty uzyskania przychodu
- **Где**: `pit.monthly_employee_costs_pln`
- **Текущее**: 250 PLN/мес (стандартная), есть повышенная 300 PLN/мес для иногородних
- **Когда обновляется**: редко, не менялось с 2022
- **Импакт**: УМЕРЕННЫЙ — небольшое влияние на PIT advance
- **Будущее**: Phase 6 добавит выбор стандартная/повышенная по водителю

### A.7 Składka zdrowotna (health insurance)
- **Где**: `zdrowotne.rate_pct`
- **Текущее**: 9.00%
- **Политически вульнерабельная**: с 2022 не вычитается из PIT (раньше вычиталась 7.75%), обсуждается возврат deductibility
- **Действие**: следить за политическими новостями про reform składki zdrowotnej

### A.8 Виртуальные диеты — ставки
- **Где**: `international_driver.{zus.rate_eur_per_day, pit.rate_eur_per_day}`
- **Текущее**: 60 EUR/день ZUS, 20 EUR/день PIT
- **Источник**: art. 21b ustawy o czasie pracy kierowców (po nowelizacji 2022)
- **Когда обновляется**: при поправке ustawy — крайне редко, нужно следить за новостями в трансе
- **Источники мониторинга**:
  - trans.info / Trans Info
  - inelo.pl/blog
  - tachospeed.pl/blog
- **Импакт**: КРИТИЧНЫЙ — изменение ставок переворачивает всю логику ZUS/PIT баз для дальнобойщиков

### A.9 Sanitariaty (ryczałt za noclegi w kabinie)
- **Где**: `sanitariaty.pln_per_day_abroad`
- **Текущее**: 60 PLN/день
- **Источник**: art. 21a ustawy o czasie pracy kierowców
- **Когда обновляется**: при поправке ustawy
- **Действие**: тот же мониторинг что A.8

---

## B. Страновые ставки (`data/country_rates/*.yaml`)

### B.1 Германия — Mindestlohn
- **Где**: `de.yaml` → period `rates.statutory_minimum.hourly` и `driver_default.hourly`
- **Текущее**: 13.90 EUR/ч (с 1.1.2026), запланирован шаг 14.60 EUR/ч с 1.1.2027 (уже в YAML)
- **Когда обновляется**: решением Mindestlohnkommission (раз в 2 года). Следующее решение ожидается в 2026 году на 2028 год.
- **Источник**:
  - https://www.mindestlohnkommission.de/
  - https://www.bmas.de/
- **Импакт**: КРИТИЧНЫЙ для расчётов с CABOTAGE/CROSS_TRADE в DE
- **Зависимости**: оба поля (`statutory_minimum` и `driver_default`) должны меняться синхронно — в Германии нет отдельной отраслевой ставки для транспорта
- **Действие**: добавить новый блок `period` в `de.yaml` с `valid_from: 2028-01-01`

### B.2 Франция — SMIC
- **Где**: `fr.yaml` → period `rates.statutory_minimum.hourly`
- **Текущее**: 12.02 EUR/ч (с 1.1.2026)
- **Когда обновляется**: ежегодно 1 января декретом. Возможна mid-year revaluation если инфляция > 2%.
- **Источник**:
  - https://www.legifrance.gouv.fr/ (поиск "SMIC")
  - https://travail-emploi.gouv.fr/
  - https://www.insee.fr/ (mzu прогнозы)
- **Импакт**: КРИТИЧНЫЙ для расчётов с FR-сегментами
- **Действие**: добавить новый период в `fr.yaml`

### B.3 Франция — CCN 3085 коэффициенты
- **Где**: `fr.yaml` → period `rates.driver_coef_*.hourly`
- **Текущее (2026)**: 110M-120M = 12.09 EUR, 138M = 12.25 EUR, 150M = 12.43 EUR, 150M-15Y = 13.42 EUR
- **Когда обновляется**: ежегодно по итогам NAO (Négociations Annuelles Obligatoires). Avenant подписывается в марте-апреле, иногда вступает задним числом с 1 января.
- **Источник**:
  - https://franceroutes.fr/ (отслеживают NAO)
  - https://www.fntr.fr/ (Fédération Nationale des Transports Routiers)
  - https://www.legifrance.gouv.fr/ (extension arrêté)
- **Импакт**: КРИТИЧНЫЙ для водителей с rate_name = `driver_coef_*`
- **Действие**: обновлять как только подписан avenant — если задним числом, пересчитать затронутые периоды (Recalculate)

### B.4 Италия — CCNL Logistica
- **Где**: `it.yaml` → period `rates.driver_b3.{monthly_gross, hourly}` и `driver_3_super.*`
- **Текущее (2026)**: B3 = 1922 PLN/мес, 3° Super = 2247 PLN/мес. На 2027 заложены 1962 / 2287.
- **Когда обновляется**: по графику CCNL. Последнее соглашение от 27.10.2025 предусматривает транши 1.1.2026, 1.1.2027, 1.6.2027.
- **Источник**:
  - https://www.contrattotrasporti.it/
  - https://www.assotir.it/
  - https://www.fai-conftrasporto.it/
- **Импакт**: КРИТИЧНЫЙ для IT-сегментов
- **Действие**: следить за datą подписания нового CCNL (текущий до середины 2027). При подписании — обновить периоды с новыми траншами.

### B.5 Остальные 27 стран ЕС (планируется в Phase 3+)
Сейчас покрыты только DE, FR, IT — главные направления вашей фирмы. Когда добавятся другие страны, для каждой нужно будет:
- Найти аналог Mindestlohn / SMIC / CCNL
- Понять график обновлений (ежегодно? по соглашениям?)
- Подписаться на профильный источник

Кандидаты на добавление в следующих фазах (по объёму трафика):
- BE (CCT trasport routier)
- NL (CAO Beroepsgoederenvervoer)
- AT (KV Güterbeförderungsgewerbe)
- ES (Convenio mercancias por carretera)
- SE / DK / NO (нет статутного, нужно по KV)

### B.6 Команда проверки свежести
```bash
docker compose exec app flask rates verify --threshold-days 90
```
Покажет страны где `verified.at` старше 90 дней. По умолчанию верификация в YAML стоит на дату когда добавили данные — каждые 3 месяца стоит зайти на сайт-источник и убедиться что не вышел avenant/decree.

---

## C. Курсы валют (`nbp_rates` в БД)

### C.1 EUR/PLN
- **Где**: таблица `nbp_rates` в БД (создаётся автоматически при `flask init-db`)
- **Источник**: NBP table A — https://api.nbp.pl/api/exchangerates/rates/A/EUR/{date}/
- **Когда обновляется**: NBP публикует курсы в каждый рабочий день (~12:00 CET)
- **Как обновляется в системе**:
  1. Автоматически при создании нового PayrollPeriod (если включена галочка "Fetch from NBP")
  2. Вручную: `flask nbp fetch EUR --on 2026-MM-DD`
- **Импакт**: КРИТИЧНЫЙ для конверсии foreign wage в PLN и виртуальных диет
- **Воспроизводимость**: курс, использованный при расчёте, сохраняется на PayrollPeriod через `nbp_rate_id`. Перерасчёт через год использует тот же курс.
- **Проверка кеша**: `flask nbp list --currency EUR --limit 30`

### C.2 Другие валюты (CZK, HUF, RON, ...)
Phase 2 поддерживает только EUR. Когда расширим страны (BG, CZ, HU, RO с национальными валютами) — потребуется:
- Многовалютный lookup в NBP API (он уже умеет CZK, HUF и т.д.)
- Расширение калькулятора (сейчас в `umowa_pracy.py` есть assert `currency == "EUR"`)
- Обновление YAML страновых ставок с правильной валютой

---

## D. Операционные данные

### D.1 Контракты водителей
- **Где**: БД `driver_contracts` таблица
- **Что отслеживаем**: поле `end_date` (если задано)
- **Импакт**: если контракт истёк, а водитель работает — `flask` калькулятор упадёт с `No active contract for ... on YYYY-MM-DD`
- **Действие**: за 30 дней до истечения создавать новый contract (можно для того же водителя), переходящий из предыдущего
- **Будущее (Phase 8)**: добавить дашбордный виджет "контракты истекают в течение 30 дней"

### D.2 Документы водителей (Phase 8)
Сейчас не реализовано. В будущем будем отслеживать сроки:
- Карта водителя (карта kierowcy)
- Прав. категория C+E (renewal каждые 5 лет)
- Świadectwo kwalifikacji zawodowej (каждые 5 лет)
- Профилактические медосмотры (каждые 1-3 года)
- Психотехнические тесты
- Karta pobytu / виза для не-EU граждан
- Паспорт

---

## E. Источники для подписки

### Польша
- **ZUS bulletin**: https://www.zus.pl/aktualnosci (RSS доступен)
- **Monitor Polski**: https://monitorpolski.gov.pl/
- **Dziennik Ustaw**: https://dziennikustaw.gov.pl/
- **inforPL**: https://www.infor.pl/ (хорошая систематизация изменений)
- **GazetaPrawna**: https://www.gazetaprawna.pl/

### Трансспедиторские (PL specifics для водителей)
- **trans.info**: https://trans.info/pl/aktualnosci (главный отраслевой источник)
- **Inelo blog**: https://inelo.pl/blog/ (от создателей TMS)
- **Tachospeed**: https://tachospeed.pl/blog/ (анализы изменений ZUS для PM)
- **Truckmobility-info**: https://truckmobility-info.com/ (мониторинг ставок по странам ЕС)

### Германия
- **BMAS**: https://www.bmas.de/
- **Mindestlohnkommission**: https://www.mindestlohnkommission.de/
- **Zoll posting portal**: https://www.zoll.de/

### Франция
- **DGT (Direction Générale du Travail)**: через travail-emploi.gouv.fr
- **FNTR**: https://www.fntr.fr/
- **France Routes**: https://franceroutes.fr/

### Италия
- **CNEL Archivio Contratti**: https://www.cnel.it/Archivio-Contratti
- **Assotir**: https://www.assotir.it/
- **ContrattoTrasporti**: https://www.contrattotrasporti.it/

### Платные опции (рекомендую для регулярного мониторинга)
- **EuroDost** — еженедельный newsletter по ставкам и изменениям во всех 27+ ЕС, около €15-30/мес
- **Inelo OCRK** — модуль внутри Inelo TMS с автоматическим оповещением о новых ставках (входит в подписку 4Trans)

---

## F. Автоматизация (что есть и что планируется)

### Что уже есть
- `flask rates verify` — показывает страны с устаревшей верификацией (>90 дней)
- `flask tax verify` — то же для польских параметров (добавляется ниже в этом обновлении)
- Автозагрузка EUR/PLN из NBP при создании периода
- Все YAML под git — изменения видны в history с автором, датой, причиной (commit message)
- `CountryRateSnapshot` — каждый расчёт фиксирует фактически использованную ставку, что гарантирует воспроизводимость

### Что планируется (Phase 8)
- Cron-задача "раз в неделю" с email-отчётом о всех stale данных
- Дашборд-виджет "контракты истекают в течение N дней"
- Дашборд-виджет "период верификации YAML истёк"
- Notification-канал (Slack/Teams webhook) при ошибке NBP fetch
- Auto-PR на обновление YAML когда RSS трэкер обнаруживает изменение источника

### Что хорошо иметь, но не приоритет
- Webhook от Monitora Polskiego (не предоставляется официально, нужен RSS-парсер)
- Интеграция с openrasada.pl или другими провайдерами для авто-фетча CCN
- Polish API для мониторинга podpisanych ustaw — есть отдельные сервисы (mojepanstwo.pl) но требуют отдельной интеграции

---

## G. Чеклист новогоднего обновления (компактная версия)

Каждый декабрь:

- [ ] Скачать `pl_NEXT_YYYY.yaml` черновик из `pl_CURRENT.yaml`
- [ ] Обновить `year:`
- [ ] `average_wage_pln_monthly` ← из M.P.
- [ ] `minimum_wage_pln_monthly` ← из Dz.U.
- [ ] `international_driver.zus.threshold_monthly_pln` = новой средней
- [ ] `international_driver.zus.floor_pln` = новой средней
- [ ] `zus_annual_cap_pln` = 30 × новой средней
- [ ] Проверить ZUS rates (обычно не меняются)
- [ ] Проверить PIT brackets (обычно не меняются)
- [ ] Обновить `verified.at` на сегодняшнюю дату
- [ ] Обновить `verified.notes` ссылками на новые публикации
- [ ] Открыть `data/country_rates/de.yaml` — если есть запланированный шаг Mindestlohn, проверить
- [ ] Открыть `fr.yaml` — добавить период для нового SMIC и (если известно) CCNTR коэффициентов
- [ ] Открыть `it.yaml` — проверить графики CCNL траншей
- [ ] `git commit -m "data: parameters for YYYY"`
- [ ] Тесты: `pytest tests/test_payroll_phase2.py`
- [ ] `docker compose down && docker compose up -d --build`
- [ ] Сделать контрольный расчёт за декабрь и за январь нового года — сверить с onlinе-калькуляторами (inforPL, inEwi, vatax.pl)
