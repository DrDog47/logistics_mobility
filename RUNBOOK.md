# Запуск Mobility Payroll с нуля

Инструкция собрана из текущего состояния проекта Phase 2: 89 файлов, Docker + Python 3.12 + Flask + SQLite + Flask-Migrate.

> **TL;DR для опытных пользователей**:
> 1. `unzip mobility_payroll.zip && cd mobility_payroll`
> 2. `cp .env.example .env && python3 -c "import secrets; print(secrets.token_hex(32))"` — вставить в `.env` как `SECRET_KEY=...`
> 3. `docker compose up -d --build`
> 4. `docker compose exec app flask init-db`
> 5. `docker compose exec app flask create-admin --login admin --email admin@example.local --name "Admin"`
> 6. `docker compose exec app flask nbp fetch EUR`
> 7. Открыть http://localhost:8000

---

## 1. Что понадобится

**Один из двух вариантов:**

- **Рекомендуемый:** Docker Engine 24+ и Docker Compose v2 ([установка для Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [для Windows](https://docs.docker.com/desktop/install/windows-install/), [для macOS](https://docs.docker.com/desktop/install/mac-install/))
- **Альтернатива (для разработки):** Python 3.12+, pip, virtualenv

**Доступ к интернету для NBP API** — нужен только при автозагрузке курсов EUR/PLN (можно вводить вручную и работать без сети).

**Архив проекта** — `mobility_payroll.zip`, который мы создавали в предыдущих шагах.

---

## 2. Распаковка и конфигурация

```bash
unzip mobility_payroll.zip
cd mobility_payroll
ls
# Dockerfile, README.md, app/, data/, requirements.txt, run.py, tests/, ...
```

Создайте файл `.env` из примера:

```bash
cp .env.example .env
```

Откройте `.env` и **обязательно** замените `SECRET_KEY` на реальный криптостойкий ключ:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# вывод вида: 7a3f9e2c8b6d1a4e0f5c2b9a8d7e6f3c1a5b9d2e8f4a7c3b6e1d8a5f2c9b7e4a
```

Вставьте этот ключ в `.env`:

```
SECRET_KEY=7a3f9e2c8b6d1a4e0f5c2b9a8d7e6f3c1a5b9d2e8f4a7c3b6e1d8a5f2c9b7e4a
```

Без `SECRET_KEY` production-конфиг откажется стартовать — это by design.

---

## 3. Запуск через Docker (рекомендуется)

### 3.1 Сборка и старт

```bash
docker compose up -d --build
```

Первая сборка займёт 2-5 минут (скачивание python:3.12-slim, установка зависимостей, компиляция переводов). Последующие запуски — 5 секунд.

Проверка что контейнер живой:

```bash
docker compose ps
# должен быть Status: Up (healthy) через ~15 секунд
docker compose logs app --tail=30
```

В логах должны увидеть:
```
[INFO] Loaded rates for DE (3 periods)
[INFO] Loaded rates for FR (2 periods)
[INFO] Loaded rates for IT (3 periods)
[INFO] Rate registry loaded: 3 countries from /app/data/country_rates
[INFO] Loaded Polish params for 2025 (avg wage 8673.00)
[INFO] Loaded Polish params for 2026 (avg wage 9420.00)
[INFO] Starting gunicorn 22.0.0
[INFO] Listening at: http://0.0.0.0:8000
```

Если видите `RuntimeError: SECRET_KEY environment variable is required` — вернитесь к разделу 2 и установите `.env`.

Если `RateRegistryError` или `PolishParamsError` — проверьте что папки `data/country_rates/` и `data/tax_rules/` распаковались.

### 3.2 Инициализация БД

```bash
docker compose exec app flask init-db
```

Должно вывести: `Database tables created.`

Файл БД создастся в `./instance/payroll.db` на хосте (volume-маппинг из docker-compose.yml). Если хотите подключиться напрямую: `sqlite3 instance/payroll.db`.

### 3.3 Создание admin'а

```bash
docker compose exec app flask create-admin \
    --login admin \
    --email admin@example.local \
    --name "Administrator"
```

Команда интерактивно запросит пароль (минимум 8 символов). Если хотите неинтерактивно, добавьте `--password "..."` (но он попадёт в историю shell).

### 3.4 Прогрев кеша NBP (опционально, но полезно)

```bash
docker compose exec app flask nbp fetch EUR
```

Должно вывести что-то вроде:
```
EUR/PLN @ 2026-06-05 = 4.2845 (table 109/A/NBP/2026)
```

Если NBP недоступен (нет сети, корпоративный firewall), `flask nbp fetch` упадёт с понятной ошибкой. В этом случае при создании периода вводите курс вручную (поле `eur_pln_rate`).

### 3.5 Проверка ставок по странам

```bash
docker compose exec app flask rates list
docker compose exec app flask rates show DE --on 2026-06-01
docker compose exec app flask rates verify --threshold-days 90
```

`verify` подскажет если какая-то страна давно не проверялась.

### 3.6 Открыть приложение

http://localhost:8000

Залогиньтесь под admin'ом, которого создали в шаге 3.3.

---

## 4. Альтернатива: запуск без Docker (для разработки)

Если Docker не подходит — например, нужно дебажить с pdb:

```bash
# В директории проекта
python3.12 -m venv .venv
source .venv/bin/activate          # Linux/macOS
# или .venv\Scripts\activate       # Windows PowerShell

pip install -r requirements.txt

# Конфиг
export FLASK_ENV=development        # запустит DevelopmentConfig (DEBUG=on)
export SECRET_KEY="dev-only-key"    # для прода нужен настоящий
export DATABASE_URL="sqlite:///$(pwd)/instance/payroll.db"

mkdir -p instance

# Инициализация БД и admin
flask --app run init-db
flask --app run create-admin --login admin --email admin@local --name Dev

# Старт
python run.py
# или: flask --app run run --port 8000
```

Откройте http://localhost:8000.

---

## 5. Прохождение первого workflow

Чтобы убедиться что всё работает, пройдите по сценарию:

### 5.1 Создать водителя

Drivers → "+ New driver":
- First name: `Иван`
- Last name: `Иванов`
- Nationality: `BLR` (или `UKR`/`POL`)
- Hire date: `2024-01-15`

После создания зайдите в карточку водителя → секция **Contracts** → "+ Add contract":
- Type: `umowa_o_prace`
- Start date: `2024-01-15`
- Base salary PLN: `5000`
- Hours norm: `168`

### 5.2 Создать фуру

Vehicles → "+ New vehicle":
- Plate: `WW 12345`
- Type: `truck`
- Make: `Mercedes`
- Model: `Actros`
- Year: `2022`

### 5.3 Создать рейс

Trips → "+ New trip":
- Driver: Иван Иванов
- Vehicle: WW 12345
- Trip number: `R-2026-03-001`
- Start date: `2026-03-01`
- End date: `2026-03-25`

После создания нажмите **+ Add segment** для каждой части маршрута:

| Date | Country | Type | Hours | Rate name |
|------|---------|------|-------|-----------|
| 2026-03-05 | DE | bilateral | 8 | driver_default |
| 2026-03-10 | DE | cabotage | 10 | driver_default |
| 2026-03-12 | FR | cross_trade | 9 | driver_coef_150m |
| 2026-03-15 | IT | cabotage | 8 | driver_b3 |
| 2026-03-18 | PL | bilateral | 8 | driver_default |

Когда сегменты внесены — кнопка **Confirm trip** (статус DRAFT → CONFIRMED). Только подтверждённые рейсы попадают в payroll.

### 5.4 Расчёт зарплаты

Payroll → "+ New period":
- Driver: Иван Иванов
- Year: `2026`, Month: `March`
- ✅ Fetch EUR/PLN from NBP automatically
- Days abroad override: оставить пустым (auto)

Submit → попадёте на страницу периода с разбивкой:

- **Foreign wage** строки по DE/FR/IT
- **Base salary** 5000 PLN
- **Equalization** (если суммы иностранной ставки превысили base)
- **Sanitariaty** = `days_abroad × 60 PLN`
- **Virtual diet ZUS** (если gross > 9420)
- **Virtual diet PIT** (всегда если есть дни за границей)
- **ZUS employee 13.71%**
- **Zdrowotne 9%**
- **PIT advance 12%**
- Итоги: Gross, ZUS base, PIT base, **Net**

Если что-то не сходится — нажмите **Recalculate**.

Когда всё проверено и подписано бухгалтером → **Approve** (период замораживается, recalculate становится недоступен).

---

## 6. Эксплуатация

### 6.1 Бэкапы

База — SQLite, лежит в `./instance/payroll.db`. Бэкап = копия файла:

```bash
# Холодный бэкап (остановите контейнер)
docker compose down
cp instance/payroll.db backups/payroll-$(date +%Y%m%d).db
docker compose up -d

# Или горячий бэкап через sqlite (требует sqlite3 на хосте)
sqlite3 instance/payroll.db ".backup backups/payroll-$(date +%Y%m%d).db"
```

Восстановление: просто положите файл обратно как `instance/payroll.db` и перезапустите.

Для production рекомендую cron'ом класть бэкапы в S3/B2/локальный NAS с retention 30+ дней.

### 6.2 Обновление ставок по странам

YAML файлы в `data/country_rates/` — это git-managed source of truth. Workflow обновления:

1. Узнаёте о новой ставке (например, повышение Mindestlohn до 14.60 с 1.1.2027 — уже в YAML)
2. Открываете `data/country_rates/de.yaml`, добавляете новый period или меняете value
3. Обновляете блок `verified.at` на сегодняшнюю дату
4. Коммит в git с ссылкой на источник в commit message
5. Деплой: `docker compose down && docker compose up -d --build`

Старые расчёты при этом остаются репродуцируемыми — `CountryRateSnapshot` хранит фактически использованное значение.

Проверить какие страны давно не верифицировали:
```bash
docker compose exec app flask rates verify --threshold-days 90
docker compose exec app flask tax verify --threshold-days 120
```

**См. также:** [`DATA_FRESHNESS.md`](DATA_FRESHNESS.md) — полный календарь актуализации с источниками, ежегодным циклом обновлений и чеклистом новогоднего апдейта.

### 6.3 Обновление польских налоговых параметров (ежегодно)

В декабре Министр финансов публикует прогнозируемое среднее вознаграждение на следующий год. Добавьте `data/tax_rules/pl_2027.yaml` по образцу `pl_2026.yaml` с новыми значениями:
- `average_wage_pln_monthly` (определяет порог для виртуальной диеты ZUS)
- `minimum_wage_pln_monthly`
- Возможные изменения в PIT / ZUS ставках

После релиза перезапустите контейнер. Расчёты за 2026 продолжат использовать `pl_2026.yaml`, за 2027 — `pl_2027.yaml`.

### 6.4 Логи

```bash
docker compose logs app -f                # tail в реальном времени
docker compose logs app --since 1h        # за последний час
docker compose logs app --tail 100 | grep ERROR
```

Расчёты payroll логируются с маркерами `Calculated <driver_id> <year>-<month>: gross=... net=...` — удобно искать конкретный период.

### 6.5 Тесты

```bash
# Внутри контейнера
docker compose exec app pytest

# Или локально
source .venv/bin/activate
pytest -v
pytest tests/test_payroll_phase2.py -v   # только Phase 2 сценарии
```

Все 17 unit-тестов должны проходить offline (httpx замокан, БД in-memory).

### 6.6 CLI команды — справочник

```bash
# Пользователи
flask create-admin --login X --email Y --name Z
flask init-db                          # только первый раз

# Страновые ставки
flask rates list                       # все страны с количеством периодов
flask rates show DE --on 2026-06-01    # детально на дату
flask rates verify --threshold-days 90 # что нужно перепроверить

# Польские налоговые параметры
flask tax show 2026                    # все параметры на год
flask tax verify --threshold-days 120  # YAML которые давно не верифицированы

# NBP курсы
flask nbp fetch EUR                    # на сегодня
flask nbp fetch EUR --on 2026-03-30    # на конкретную дату
flask nbp list --currency EUR --limit 50  # что в кеше
```

В Docker префиксируйте всё `docker compose exec app `.

---

## 7. Решение типичных проблем

| Симптом | Причина | Что делать |
|---------|---------|------------|
| `RuntimeError: SECRET_KEY environment variable is required` | Нет `.env` или пустой ключ | Раздел 2: создайте `.env` с реальным ключом |
| `RateRegistryError: No country rate YAML files found` | Не примонтирована `data/` | Проверьте `docker-compose.yml`, должен быть volume `./data:/app/data:ro` |
| `PolishParamsError: No pl_YYYY.yaml files found` | То же что выше, или удалили tax YAML | Проверьте что `data/tax_rules/pl_2026.yaml` существует |
| 500 ошибка при логине: `no such table: users` | Не запустили `flask init-db` | `docker compose exec app flask init-db` |
| `NbpError: NBP API request failed` | Нет интернета, или ваш регион блокирует api.nbp.pl | При создании периода снимите галочку auto-fetch и введите курс вручную |
| `NbpError: NBP API returned 404 for EUR ...` через 11 дней walkback | Запросили слишком старую/будущую дату | Используйте дату не далее +/- недели от сегодня |
| Calculation failed: `EUR/PLN exchange rate must be set` | Курс 0 или не передан | Введите в форме периода нормальный курс (3-6) |
| Calculation failed: `No active contract for ... on YYYY-MM-DD` | У водителя нет контракта на конец месяца расчёта | Создайте/продлите контракт в карточке водителя |
| `Period already exists for this driver and month` | Уникальный констрейнт `(driver_id, year, month)` | Откройте существующий период вместо создания нового |
| Контейнер падает циклично | Что-то в YAML невалидно | `docker compose logs app --tail 50` покажет точную ошибку Marshmallow |
| Изменил YAML, но новые ставки не появляются | Реестр загружается один раз при старте | `docker compose restart app` |

---

## 8. Что дальше

Phase 2 закрывает базовый scope расчёта зарплаты по Пакету мобильности для umowa o pracę. Следующие фазы добавят (порядок согласовываем):

- **Phase 3** — парсер тахографических `.DDD` файлов (заменит ручной ввод сегментов)
- **Phase 7** — PDF payslips + Excel выгрузки + IMI CSV (для бухгалтера и польских властей)
- **Phase 4** — GPS-классификация сегментов с возможностью ручной правки
- **Phase 5** — umowa zlecenia и B2B контракты
- **Phase 6** — overtime, ночные часы, отпуска, больничные

---

## 9. Деинсталляция / полный сброс

```bash
docker compose down -v          # удалит контейнер и сети (но не данные)
rm -rf instance/                # удалит БД (НЕОБРАТИМО)
docker image rm mobility_payroll-app  # удалит образ
```

После этого, чтобы вернуться к работе — повторите с шага 2.
