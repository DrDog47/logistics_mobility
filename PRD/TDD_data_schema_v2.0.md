# Техническое задание
## Схема хранения данных — PostgreSQL
### Система учёта документов водителей и транспорта

*Логистическая компания (г. Белосток, Польша) · версия 2.0*

> **Версия 2.0** актуализирует схему по фактической реализации (Flask + SQLAlchemy + Alembic). Перечень изменений относительно v1.0 — в разделе [16. Изменения относительно версии 1.0](#16-изменения-относительно-версии-10).

---

## 1. Контекст

Документ описывает схему хранения данных Python-сервиса (Flask/Jinja), заменяющего прежнюю систему на Google Sheets + Apps Script.

- **СУБД (production):** PostgreSQL. Расширение `pgcrypto` (`gen_random_uuid()`) включается первой миграцией; UUID-первичные ключи используют `gen_random_uuid()` как server-default.
- **СУБД (тесты):** SQLite. Портируемые типы (`Uuid(as_uuid=True)`, `JSON.with_variant(JSONB)`) обеспечивают работу одних и тех же моделей на обеих СУБД. UUID на SQLite хранится как `CHAR(32)`, `JSONB` — как `JSON`/`TEXT`.
- **ORM-слой:** SQLAlchemy 2.x (`Mapped` / `mapped_column`), миграции — Alembic (каталог `migrations/`).

Схема реализована в коде (`app/**/models.py`) и зафиксирована миграциями:

| Revision | Файл | Содержание |
|----------|------|-----------|
| `67930e7cce7a` | `initial_postgres_prd_schema` | Базовая схема: organisation, users, drivers, vehicles, документы, контракты, рейсы, курсы |
| `a1b2c3d4e5f6` | `document_type_catalogue` | Каталог `document_type`, составной FK, `file_link → file_links` (JSONB-массив) |
| `e5f6a7b8c9d0` | `driver_file_table` | Таблица `driver_file` (1-ко-многим), перенос `driver_document.file_links` в строки, удаление колонки |

---

## 2. Глобальные соглашения

### 2.1. Стандартный набор полей PRD (`PrdStandardMixin`)

Доменные таблицы (organisation, drivers, vehicles, document_type, driver_document, vehicle_document) наследуют четыре стандартных поля:

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `uuid` | `UUID` | `PRIMARY KEY`, server-default `gen_random_uuid()`, Python-default `uuid4()` | Уникальный идентификатор записи |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, server-default `now()` | Момент создания записи |
| `deleted_at` | `TIMESTAMPTZ` | — | Момент мягкого удаления; `NULL` — запись активна |
| `is_deleted` | `BOOLEAN` | `NOT NULL`, server-default `false` | Флаг мягкого удаления |

> **Python-default для `uuid`:** `default=uuid.uuid4` на уровне приложения — вставка не зависит от БД (нужно для SQLite). Server-default `gen_random_uuid()` присутствует только в DDL миграций.

> **Мягкое удаление (soft delete):** помощник `soft_delete()` ставит `is_deleted = TRUE` и `deleted_at = NOW()`. Записи физически не удаляются; **каждый запрос обязан фильтровать `is_deleted IS FALSE`**.

### 2.2. Поле `updated_at` (`UpdatedAtMixin`)

`updated_at` (`TIMESTAMPTZ NOT NULL`, server-default `now()`, ORM `onupdate=now()`) добавляется **только** к таблицам `organisation` и `drivers` — это редактируемые справочники, где важен момент последнего изменения. Поддерживается на ORM-слое (`onupdate`), а не триггером БД — ради паритета с SQLite в тестах.

### 2.3. Технические (служебные) таблицы

Таблицы `users`, `driver_contracts`, `trips`, `trip_segments`, `nbp_rates`, `country_rate_snapshots` **не** используют PRD-набор: у них целочисленный автоинкрементный `id` (`BIGINT/INTEGER PRIMARY KEY`) и собственный `created_at`/`fetched_at`. Они служебные/транзакционные, мягкое удаление к ним не применяется.

### 2.4. Соглашения по именованию

- Доменные сущности с историей в Google Sheets — во множественном числе: `drivers`, `vehicles`. Таблицы-справочники/связи PRD — в единственном: `organisation`, `driver_document`, `vehicle_document`, `document_type`.
- FK на PRD-таблицы именуются `<сущность>_uuid` (`organisation_uuid`, `driver_uuid`); FK на технические таблицы — `<сущность>_id` (`driver_id`, `trip_id`).
- Индексы Alembic — `ix_<таблица>_<колонка>`; именованные ограничения — `uq_…` / `fk_…`.

### 2.5. Часовой пояс

Все `TIMESTAMPTZ` хранятся в UTC (`datetime.now(UTC)` на уровне приложения).

---

## 3. Таблица `organisation` — Организации

Компании-работодатели, к которым относятся водители и транспортные средства.

### 3.1. DDL

```sql
CREATE TABLE organisation (
    uuid            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    is_deleted      BOOLEAN         NOT NULL DEFAULT FALSE,

    national_id     VARCHAR(100)    NOT NULL,
    name            VARCHAR(255)    NOT NULL,
    country         CHAR(3)         NOT NULL,
    city            VARCHAR(100)    NOT NULL,
    address         VARCHAR(255)    NOT NULL
);

ALTER TABLE organisation ADD CONSTRAINT uq_organisation_national_id UNIQUE (national_id);
CREATE INDEX ix_organisation_country ON organisation (country);
```

### 3.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `uuid` | `UUID` | `PK` | Автогенерируемый идентификатор |
| 2 | `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент создания записи |
| 3 | `updated_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент последнего обновления |
| 4 | `deleted_at` | `TIMESTAMPTZ` | — | Момент мягкого удаления |
| 5 | `is_deleted` | `BOOLEAN` | `NOT NULL` | Флаг мягкого удаления |
| 6 | `national_id` | `VARCHAR(100)` | `NOT NULL, UNIQUE` | Национальный идентификатор (NIP в PL, ИНН в RU, EDRPOU в UA …) |
| 7 | `name` | `VARCHAR(255)` | `NOT NULL` | Официальное название компании |
| 8 | `country` | `CHAR(3)` | `NOT NULL` | Страна регистрации, ISO 3166-1 alpha-3 |
| 9 | `city` | `VARCHAR(100)` | `NOT NULL` | Город |
| 10 | `address` | `VARCHAR(255)` | `NOT NULL` | Юридический адрес |

### 3.3. Примечания

- `national_id` — уникальный деловой ключ; формат зависит от страны, валидация на уровне приложения.
- `ON DELETE RESTRICT` на FK из `drivers` и `vehicles`: физическое удаление организации заблокировано, пока к ней привязаны водители или ТС.

---

## 4. Таблица `users` — Пользователи системы

Учётные записи сотрудников с доступом к системе. Управление доступом по ролям (RBAC). Таблица **не** PRD-стандартная: целочисленный `id`, без мягкого удаления.

### 4.1. DDL

```sql
CREATE TABLE users (
    id              SERIAL          PRIMARY KEY,
    login           VARCHAR(64)     NOT NULL,
    email           VARCHAR(254)    NOT NULL,
    password_hash   VARCHAR(128)    NOT NULL,
    full_name       VARCHAR(128)    NOT NULL,
    role            role_enum       NOT NULL,         -- admin | accountant | fleet_manager
    is_active       BOOLEAN         NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL,
    last_login_at   TIMESTAMPTZ
);

ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
CREATE UNIQUE INDEX ix_users_login ON users (login);
```

### 4.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `id` | `INTEGER` | `PK` | Идентификатор пользователя |
| 2 | `login` | `VARCHAR(64)` | `NOT NULL, UNIQUE` | Логин для входа |
| 3 | `email` | `VARCHAR(254)` | `NOT NULL, UNIQUE` | Email |
| 4 | `password_hash` | `VARCHAR(128)` | `NOT NULL` | bcrypt-хеш пароля (12 раундов) |
| 5 | `full_name` | `VARCHAR(128)` | `NOT NULL` | ФИО сотрудника |
| 6 | `role` | `ENUM` | `NOT NULL` | Роль: `admin` / `accountant` / `fleet_manager` |
| 7 | `is_active` | `BOOLEAN` | `NOT NULL` | Активность учётной записи |
| 8 | `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент создания |
| 9 | `last_login_at` | `TIMESTAMPTZ` | — | Момент последнего входа |

### 4.3. Роли (`Role`)

| Значение | Уровень доступа |
|----------|-----------------|
| `admin` | Полный доступ |
| `accountant` | Бухгалтерия / расчёт ЗП |
| `fleet_manager` | Управление автопарком и документами (по умолчанию) |

---

## 5. Таблица `drivers` — Водители

Персональные и кадровые данные водителей. PRD-стандартная таблица + `updated_at`.

### 5.1. DDL

```sql
CREATE TABLE drivers (
    uuid                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at              TIMESTAMPTZ,
    is_deleted              BOOLEAN       NOT NULL DEFAULT FALSE,

    -- Идентификация (first_name/last_name — латинское написание из паспорта)
    first_name              VARCHAR(64)   NOT NULL,
    last_name               VARCHAR(64)   NOT NULL,
    birth_date              DATE          NOT NULL,
    nationality             CHAR(3)       NOT NULL,
    identification_id       VARCHAR(30)   NOT NULL,
    pesel                   VARCHAR(11),
    passport_number         VARCHAR(32),
    tachograph_card_number  VARCHAR(20),

    -- Контакты / заметки
    phone                   VARCHAR(32),
    notes                   VARCHAR(1000),

    -- Кадровые данные
    hire_date               DATE          NOT NULL,
    termination_date        DATE,
    is_active               BOOLEAN       NOT NULL DEFAULT TRUE,

    -- Связь с организацией
    organisation_uuid       UUID          NOT NULL,

    extra                   JSONB
);

ALTER TABLE drivers ADD CONSTRAINT drivers_identification_id_key       UNIQUE (identification_id);
ALTER TABLE drivers ADD CONSTRAINT drivers_pesel_key                   UNIQUE (pesel);
ALTER TABLE drivers ADD CONSTRAINT drivers_tachograph_card_number_key  UNIQUE (tachograph_card_number);
ALTER TABLE drivers ADD CONSTRAINT fk_drivers_organisation
    FOREIGN KEY (organisation_uuid) REFERENCES organisation (uuid) ON DELETE RESTRICT;

CREATE INDEX ix_drivers_last_name         ON drivers (last_name);
CREATE INDEX ix_drivers_organisation_uuid ON drivers (organisation_uuid);
```

### 5.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `uuid` | `UUID` | `PK` | Идентификатор водителя |
| 2 | `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент создания |
| 3 | `updated_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент последнего обновления |
| 4 | `deleted_at` | `TIMESTAMPTZ` | — | Мягкое удаление |
| 5 | `is_deleted` | `BOOLEAN` | `NOT NULL` | Флаг мягкого удаления |
| 6 | `first_name` | `VARCHAR(64)` | `NOT NULL` | Имя латиницей (как в паспорте) |
| 7 | `last_name` | `VARCHAR(64)` | `NOT NULL`, индекс | Фамилия латиницей |
| 8 | `birth_date` | `DATE` | `NOT NULL` | Дата рождения |
| 9 | `nationality` | `CHAR(3)` | `NOT NULL` | Гражданство, ISO 3166-1 alpha-3 |
| 10 | `identification_id` | `VARCHAR(30)` | `NOT NULL, UNIQUE` | Номер паспорта — деловой ключ |
| 11 | `pesel` | `VARCHAR(11)` | `UNIQUE`, nullable | Польский номер PESEL |
| 12 | `passport_number` | `VARCHAR(32)` | nullable | Номер паспорта (отдельно от `identification_id`) |
| 13 | `tachograph_card_number` | `VARCHAR(20)` | `UNIQUE`, nullable | Номер карты тахографа |
| 14 | `phone` | `VARCHAR(32)` | nullable | Контактный телефон |
| 15 | `notes` | `VARCHAR(1000)` | nullable | Заметки |
| 16 | `hire_date` | `DATE` | `NOT NULL` | Дата приёма на работу |
| 17 | `termination_date` | `DATE` | nullable | Дата увольнения |
| 18 | `is_active` | `BOOLEAN` | `NOT NULL` | Действующий сотрудник |
| 19 | `organisation_uuid` | `UUID` | `NOT NULL, FK → organisation` | Организация-работодатель |
| 20 | `extra` | `JSONB` | — | Расширяемые атрибуты (валидация на уровне приложения) |

### 5.3. Примечания

- Ряд атрибутов, бывших в v1.0 ключами `extra` (`pesel`, `phone`, `notes`), вынесены в отдельные колонки — они оказались общими для всех водителей. `extra` остаётся для будущих необязательных атрибутов.
- `updated_at` поддерживается через ORM `onupdate`, а не триггер БД.
- Один водитель не зависит от конкретного договора: договоры вынесены в `driver_contracts` (§10), история рейсов — в `trips` (§11).

---

## 6. Таблица `vehicles` — Транспортные средства

Тягачи (фуры) и прицепы. PRD-стандартная таблица (без `updated_at`).

### 6.1. DDL

```sql
CREATE TABLE vehicles (
    uuid                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ,
    is_deleted          BOOLEAN       NOT NULL DEFAULT FALSE,

    vehicle_type        VARCHAR(20)   NOT NULL,    -- tractor | trailer
    vin                 VARCHAR(17)   NOT NULL,
    brand               VARCHAR(100)  NOT NULL,
    model               VARCHAR(100)  NOT NULL,
    registration_plate  VARCHAR(20)   NOT NULL,
    acquisition_date    DATE,
    manufacture_date    DATE,
    is_active           BOOLEAN       NOT NULL DEFAULT TRUE,

    organisation_uuid   UUID          NOT NULL,
    extra               JSONB
);

ALTER TABLE vehicles ADD CONSTRAINT vehicles_vin_key UNIQUE (vin);
ALTER TABLE vehicles ADD CONSTRAINT fk_vehicles_organisation
    FOREIGN KEY (organisation_uuid) REFERENCES organisation (uuid) ON DELETE RESTRICT;

CREATE INDEX ix_vehicles_vehicle_type       ON vehicles (vehicle_type);
CREATE INDEX ix_vehicles_registration_plate ON vehicles (registration_plate);
CREATE INDEX ix_vehicles_organisation_uuid  ON vehicles (organisation_uuid);
```

### 6.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `uuid` | `UUID` | `PK` | Идентификатор ТС |
| 2 | `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент создания |
| 3 | `deleted_at` | `TIMESTAMPTZ` | — | Мягкое удаление |
| 4 | `is_deleted` | `BOOLEAN` | `NOT NULL` | Флаг мягкого удаления |
| 5 | `vehicle_type` | `VARCHAR(20)` | `NOT NULL`, индекс | `tractor` (тягач) / `trailer` (прицеп) |
| 6 | `vin` | `VARCHAR(17)` | `NOT NULL, UNIQUE` | VIN-номер |
| 7 | `brand` | `VARCHAR(100)` | `NOT NULL` | Марка |
| 8 | `model` | `VARCHAR(100)` | `NOT NULL` | Модель |
| 9 | `registration_plate` | `VARCHAR(20)` | `NOT NULL`, индекс | Регистрационный знак |
| 10 | `acquisition_date` | `DATE` | nullable | Дата приобретения |
| 11 | `manufacture_date` | `DATE` | nullable | Дата выпуска |
| 12 | `is_active` | `BOOLEAN` | `NOT NULL` | ТС в эксплуатации |
| 13 | `organisation_uuid` | `UUID` | `NOT NULL, FK → organisation` | Владелец |
| 14 | `extra` | `JSONB` | — | Расширяемые атрибуты |

### 6.3. Допустимые значения `vehicle_type`

| Значение | Описание |
|----------|----------|
| `tractor` | Тягач (фура, грузовик) |
| `trailer` | Прицеп |

`vehicle_type` — `VARCHAR`, не `ENUM`, для симметрии с `document_type`.

---

## 7. Таблица `document_type` — Каталог типов документов

Операторо-редактируемый справочник типов документов. Заменяет хардкод-перечни v1.0: новые типы добавляются из UI. Ключ `(type, entity_type)` уникален и служит целью составного FK документов.

### 7.1. DDL

```sql
CREATE TABLE document_type (
    uuid          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE,

    type          VARCHAR(30)   NOT NULL,   -- код типа: passport, insurance, ...
    entity_type   VARCHAR(20)   NOT NULL,   -- driver | vehicle | organisation
    label         VARCHAR(100)              -- человекочитаемое имя; fallback на type
);

ALTER TABLE document_type
    ADD CONSTRAINT uq_document_type_type_entity UNIQUE (type, entity_type);
```

### 7.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1–4 | стандартный набор PRD | | | uuid / created_at / deleted_at / is_deleted |
| 5 | `type` | `VARCHAR(30)` | `NOT NULL` | Код типа документа (хранится в документах) |
| 6 | `entity_type` | `VARCHAR(20)` | `NOT NULL` | Принадлежность: `driver` / `vehicle` / `organisation` |
| 7 | `label` | `VARCHAR(100)` | — | Отображаемое имя; при `NULL` берётся `type` |

> `(type, entity_type)` уникально — один и тот же код может существовать для разных сущностей. Составной FK документов ссылается именно на эту пару.

### 7.3. Базовый каталог (seed)

Таблица засевается из `app/documents/constants.py` (CLI `seed-document-types` и миграция). Базовый перечень:

**`entity_type = driver`:**

| `type` | Метка (label) |
|--------|---------------|
| `passport` | Passport (non-EU) |
| `passport_eu` | Passport (EU citizen) |
| `visa` | Visa |
| `residence` | Residence card (karta pobytu) |
| `license` | Driving licence |
| `code95` | Driver qualification card (code 95) |
| `medical` | Medical exam (badania lekarskie) |
| `psychological` | Psychological exam (badania psychologiczne) |
| `tacho_card` | Tachograph card (karta kierowcy) |
| `adr` | ADR certificate |
| `pesel` | PESEL notification |
| `oswiadczenie` | Work permit (oświadczenie) |
| `employment` | Employment contract |
| `employment_annex` | Employment contract annex |

**`entity_type = vehicle`:**

| `type` | Метка (label) | Контроль срока |
|--------|---------------|----------------|
| `tech_passport` | Tech passport | Нет (бессрочный) |
| `inspection` | Technical inspection | Да — 120 и 60 дней |
| `insurance` | Insurance | Да — 60, 30, 15 дней |

**`entity_type = organisation`:** базовый перечень пуст — зарезервирован под будущие документы организаций (таблица `organisation_document` пока не реализована).

### 7.4. Пороги уведомлений и нетрекаемые типы

Конфигурация контроля сроков — в `app/documents/constants.py`:

- `GENERIC_THRESHOLDS = (120, 60)` — пороги (дней до `end_date`) для большинства документов.
- `INSURANCE_THRESHOLDS = (60, 30, 15)` — более частая шкала для страховки ТС.
- `UNTRACKED_TYPES = {employment, pesel, tech_passport}` — типы, чей срок не отслеживается (бессрочные/идентификационные). `employment_annex` отслеживается только при наличии `end_date`.

---

## 8. Таблица `driver_document` — Документы водителя

Документы, привязанные к водителю (один-ко-многим). PRD-стандартная таблица + составной FK на каталог.

### 8.1. DDL

```sql
CREATE TABLE driver_document (
    uuid          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE,

    document_type VARCHAR(30)   NOT NULL,
    entity_type   VARCHAR(20)   NOT NULL DEFAULT 'driver',  -- дискриминатор для составного FK
    document_id   VARCHAR(100),
    start_date    DATE,
    end_date      DATE,
    extra         JSONB,
    -- Файлы документа вынесены в отдельную таблицу driver_file (см. §8a).

    driver_uuid   UUID          NOT NULL
);

ALTER TABLE driver_document ADD CONSTRAINT fk_driver_document_driver
    FOREIGN KEY (driver_uuid) REFERENCES drivers (uuid) ON DELETE RESTRICT;

ALTER TABLE driver_document ADD CONSTRAINT fk_driver_document_type
    FOREIGN KEY (document_type, entity_type)
    REFERENCES document_type (type, entity_type) ON DELETE RESTRICT;

CREATE INDEX ix_driver_document_document_type ON driver_document (document_type);
CREATE INDEX ix_driver_document_driver_uuid   ON driver_document (driver_uuid);
```

### 8.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1–4 | стандартный набор PRD | | | uuid / created_at / deleted_at / is_deleted |
| 5 | `document_type` | `VARCHAR(30)` | `NOT NULL`, индекс, FK | Код типа (часть составного FK на каталог) |
| 6 | `entity_type` | `VARCHAR(20)` | `NOT NULL`, default `'driver'` | Константа-дискриминатор; вторая часть составного FK |
| 7 | `document_id` | `VARCHAR(100)` | nullable | Номер документа (может отсутствовать) |
| 8 | `start_date` | `DATE` | nullable | Начало действия |
| 9 | `end_date` | `DATE` | nullable | Окончание действия |
| 10 | `extra` | `JSONB` | nullable | Атрибуты, специфичные для типа документа |
| 11 | `driver_uuid` | `UUID` | `NOT NULL, FK → drivers` | Водитель-владелец |

> Файлы документа (сканы) хранятся в отдельной таблице **`driver_file`** (один-ко-многим, §8a), а не массивом `file_links`. У `vehicle_document` массив `file_links JSONB` пока сохранён (§9).

### 8.3. Допустимые ключи `extra` по типам

| `document_type` | Ключ | Тип | Описание |
|-----------------|------|-----|----------|
| `license` | `categories` | `string` | Категории прав, например `"C+E"` |
| `employment_annex` | `base_contract_id` | `string` | Номер базового трудового договора |
| `oswiadczenie` | `pup_number` | `string` | Номер документа PUP (`PoOs/<код>/<год>/<номер>`) |
| `oswiadczenie` | `position` | `string` | Должность в разрешении |
| `pesel` | `pesel_number` | `string` | Номер PESEL |
| `passport`, `passport_eu`, `residence` | `issuing_country` | `string` | Страна выдачи (ISO 3166-1 alpha-3) |

> Список не исчерпывающий — расширяется без миграции. Валидация структуры `extra` — на уровне приложения.

### 8.4. Примечания

- Файлы документа вынесены из массива `file_links JSONB` (v2.0) в отдельную таблицу `driver_file` (§8a) — один документ ↔ много файлов, у каждого файла своя распознанная мета.
- Составной FK `(document_type, entity_type) → document_type(type, entity_type)` гарантирует, что в документ нельзя записать тип, отсутствующий в каталоге для нужной сущности.
- `ON DELETE RESTRICT` на `driver_uuid`: физическое удаление водителя заблокировано, пока есть документы (страховка поверх мягкого удаления).

---

## 8a. Таблица `driver_file` — Файлы документа водителя

Физические файлы (сканы), относящиеся к одному `driver_document` (один-ко-многим). Drag-n-drop добавляет в документ один или несколько файлов; каждому файлу соответствует строка `driver_file`. PRD-стандартная таблица.

### 8a.1. DDL

```sql
CREATE TABLE driver_file (
    uuid          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE,

    document_type VARCHAR(30),                              -- если распознан
    document_id   VARCHAR(100),                             -- если распознан
    start_date    DATE,                                     -- если распознана
    end_date      DATE,                                     -- если распознана
    file_link     TEXT          NOT NULL,                   -- место хранения файла
    extra         JSONB,                                    -- распознанные атрибуты файла

    document_uuid UUID          NOT NULL
);

ALTER TABLE driver_file ADD CONSTRAINT fk_driver_file_document
    FOREIGN KEY (document_uuid) REFERENCES driver_document (uuid) ON DELETE CASCADE;

CREATE INDEX ix_driver_file_document_uuid ON driver_file (document_uuid);
```

### 8a.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1–4 | стандартный набор PRD | | | uuid / created_at / deleted_at / is_deleted |
| 5 | `document_type` | `VARCHAR(30)` | nullable | Тип документа, распознанный по файлу (если определился) |
| 6 | `document_id` | `VARCHAR(100)` | nullable | Номер документа (если определился) |
| 7 | `start_date` | `DATE` | nullable | Начало действия (если определилось) |
| 8 | `end_date` | `DATE` | nullable | Окончание действия (если определилось) |
| 9 | `file_link` | `TEXT` | `NOT NULL` | Место хранения файла (относительный путь / URL) |
| 10 | `extra` | `JSONB` | nullable | Распознанные атрибуты файла без жёсткой структуры |
| 11 | `document_uuid` | `UUID` | `NOT NULL, FK → driver_document` | Документ-владелец (1-ко-многим) |

### 8a.3. Примечания

- **Документ создаётся вместе с первым файлом.** Если при распознавании тип файла определён, а документа ещё нет — при записи файла сначала создаётся `driver_document`, затем его `uuid` подставляется в `driver_file.document_uuid` (в рамках одной транзакции Postgres).
- **Нет `document_uuid` — нет строки.** Если файлу не удаётся присвоить документ (не определены водитель или документ автоматически), файл **остаётся в `_Inbox/` для ручной обработки** и строка `driver_file` не создаётся (`document_uuid NOT NULL`).
- `ON DELETE CASCADE`: файлы — собственность документа; физическое удаление документа удаляет его файлы. Мягкое удаление (`is_deleted`) — основной режим.

---

## 9. Таблица `vehicle_document` — Документы транспортного средства

Зеркалит `driver_document`; общие колонки — в общем mixin'е на уровне ORM.

### 9.1. DDL

```sql
CREATE TABLE vehicle_document (
    uuid          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE,

    document_type VARCHAR(30)   NOT NULL,
    entity_type   VARCHAR(20)   NOT NULL DEFAULT 'vehicle',
    document_id   VARCHAR(100),
    start_date    DATE,
    end_date      DATE,
    file_links    JSONB,
    extra         JSONB,

    vehicle_uuid  UUID          NOT NULL
);

ALTER TABLE vehicle_document ADD CONSTRAINT fk_vehicle_document_vehicle
    FOREIGN KEY (vehicle_uuid) REFERENCES vehicles (uuid) ON DELETE RESTRICT;

ALTER TABLE vehicle_document ADD CONSTRAINT fk_vehicle_document_type
    FOREIGN KEY (document_type, entity_type)
    REFERENCES document_type (type, entity_type) ON DELETE RESTRICT;

CREATE INDEX ix_vehicle_document_document_type ON vehicle_document (document_type);
CREATE INDEX ix_vehicle_document_vehicle_uuid  ON vehicle_document (vehicle_uuid);
```

### 9.2. Допустимые ключи `extra` по типам

| `document_type` | Ключ | Тип | Описание |
|-----------------|------|-----|----------|
| `insurance` | `insurance_company` | `string` | Название страховой компании |
| `inspection` | `periodicity_months` | `integer` | Периодичность техосмотра в месяцах (по умолчанию 12) |

Структура полей идентична `driver_document`, кроме связи (`vehicle_uuid` вместо `driver_uuid`) и значения дискриминатора (`entity_type = 'vehicle'`).

---

## 10. Таблица `driver_contracts` — Договоры водителей

История договоров водителя (один-ко-многим). Техническая таблица (целочисленный `id`).

### 10.1. DDL

```sql
CREATE TABLE driver_contracts (
    id              SERIAL          PRIMARY KEY,
    driver_id       UUID            NOT NULL,
    contract_type   contracttype    NOT NULL,    -- umowa_o_prace | umowa_zlecenia | b2b
    start_date      DATE            NOT NULL,
    end_date        DATE,
    base_salary_pln NUMERIC(10,2)   NOT NULL,
    hours_norm      INTEGER         NOT NULL DEFAULT 168,
    created_at      TIMESTAMPTZ     NOT NULL
);

ALTER TABLE driver_contracts ADD CONSTRAINT fk_driver_contracts_driver
    FOREIGN KEY (driver_id) REFERENCES drivers (uuid) ON DELETE CASCADE;

CREATE INDEX ix_driver_contracts_driver_id ON driver_contracts (driver_id);
```

### 10.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `id` | `INTEGER` | `PK` | Идентификатор договора |
| 2 | `driver_id` | `UUID` | `NOT NULL, FK → drivers`, индекс | Водитель |
| 3 | `contract_type` | `ENUM` | `NOT NULL` | Тип договора по польскому праву |
| 4 | `start_date` | `DATE` | `NOT NULL` | Начало действия |
| 5 | `end_date` | `DATE` | nullable | Окончание (`NULL` — бессрочный) |
| 6 | `base_salary_pln` | `NUMERIC(10,2)` | `NOT NULL` | Базовая ставка в PLN (брутто для KP/zlecenia, договорная для B2B) |
| 7 | `hours_norm` | `INTEGER` | `NOT NULL`, default 168 | Месячная норма часов (для KP-договоров; игнорируется для B2B) |
| 8 | `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент создания |

### 10.3. Типы договоров (`ContractType`)

| Значение | Описание |
|----------|----------|
| `umowa_o_prace` | Трудовой договор — полный ZUS + PIT |
| `umowa_zlecenia` | Договор поручения — частичные сценарии ZUS |
| `b2b` | Самозанятость — счета, без правил KP-зарплаты |

> FK `ON DELETE CASCADE`: физическое удаление водителя удаляет его договоры. В обычной работе используется мягкое удаление водителя.

---

## 11. Таблица `trips` — Рейсы

Логический рейс (например, PL → DE → FR → PL) на несколько дней. Заголовок для сегментов.

### 11.1. DDL

```sql
CREATE TABLE trips (
    id           SERIAL        PRIMARY KEY,
    driver_id    UUID          NOT NULL,
    vehicle_id   UUID,
    trip_number  VARCHAR(32)   NOT NULL,
    start_date   DATE          NOT NULL,
    end_date     DATE          NOT NULL,
    notes        VARCHAR(1000),
    status       tripstatus    NOT NULL,        -- draft | confirmed
    created_at   TIMESTAMPTZ   NOT NULL
);

ALTER TABLE trips ADD CONSTRAINT fk_trips_driver
    FOREIGN KEY (driver_id) REFERENCES drivers (uuid) ON DELETE RESTRICT;
ALTER TABLE trips ADD CONSTRAINT fk_trips_vehicle
    FOREIGN KEY (vehicle_id) REFERENCES vehicles (uuid) ON DELETE SET NULL;

CREATE INDEX ix_trips_driver_id   ON trips (driver_id);
CREATE INDEX ix_trips_trip_number ON trips (trip_number);
```

### 11.2. Описание полей

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `id` | `INTEGER` | `PK` | Идентификатор рейса |
| 2 | `driver_id` | `UUID` | `NOT NULL, FK → drivers`, индекс | Водитель |
| 3 | `vehicle_id` | `UUID` | `FK → vehicles`, nullable | ТС (`SET NULL` при удалении ТС) |
| 4 | `trip_number` | `VARCHAR(32)` | `NOT NULL`, индекс | Номер рейса |
| 5 | `start_date` | `DATE` | `NOT NULL` | Дата начала |
| 6 | `end_date` | `DATE` | `NOT NULL` | Дата окончания |
| 7 | `notes` | `VARCHAR(1000)` | nullable | Примечания |
| 8 | `status` | `ENUM` | `NOT NULL` | `draft` / `confirmed` (confirmed — заблокирован, попадает в расчёт ЗП) |
| 9 | `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент создания |

---

## 12. Таблица `trip_segments` — Сегменты рейса

Один непрерывный отрезок работы в ОДНОЙ стране с ОДНОЙ классификацией Mobility Package. Вручную в Phase 1; автогенерация из тахографа + GPS в Phase 4.

### 12.1. DDL

```sql
CREATE TABLE trip_segments (
    id            SERIAL        PRIMARY KEY,
    trip_id       INTEGER       NOT NULL,
    sequence      INTEGER       NOT NULL DEFAULT 0,
    work_date     DATE          NOT NULL,
    country       CHAR(2)       NOT NULL,        -- ISO 3166-1 alpha-2
    segment_type  segmenttype   NOT NULL,        -- transit | bilateral | cabotage | cross_trade
    work_hours    NUMERIC(5,2)  NOT NULL,
    rate_name     VARCHAR(64)   NOT NULL DEFAULT 'driver_default',
    notes         VARCHAR(500)
);

ALTER TABLE trip_segments ADD CONSTRAINT fk_trip_segments_trip
    FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE;

CREATE INDEX ix_trip_segments_trip_id   ON trip_segments (trip_id);
CREATE INDEX ix_trip_segments_country   ON trip_segments (country);
CREATE INDEX ix_trip_segments_work_date ON trip_segments (work_date);
```

### 12.2. Классификация сегмента (`SegmentType`)

| Значение | Описание | Posting* |
|----------|----------|----------|
| `transit` | Транзит без погрузки/выгрузки | Нет |
| `bilateral` | PL ↔ X (до 2 доп. остановок) | Нет |
| `cabotage` | X → X (перевозка внутри одной иностранной страны) | **Да** |
| `cross_trade` | X → Y (между двумя иностранными странами) | **Да** |

\* *Posting* — срабатывание Posted Workers Directive (выравнивание иностранной секторной ставки и регистрация IMI). Только `cabotage` и `cross_trade` («delegowanie»).

> `rate_name` ссылается на ставку в YAML-конфигурации ставок (не таблица БД). Зафиксированное значение ставки переносится в `country_rate_snapshots` при расчёте ЗП.

---

## 13. Таблица `nbp_rates` — Кэш курсов НБП

Кэш курсов НБП (таблица A): по одному ряду на пару (валюта, дата). Заполняется из API НБП по требованию для воспроизводимости расчётов ЗП.

### 13.1. DDL

```sql
CREATE TABLE nbp_rates (
    id              SERIAL        PRIMARY KEY,
    currency        CHAR(3)       NOT NULL,
    effective_date  DATE          NOT NULL,
    rate_pln        NUMERIC(10,4) NOT NULL,
    table_no        VARCHAR(20),
    fetched_at      TIMESTAMPTZ   NOT NULL
);

ALTER TABLE nbp_rates ADD CONSTRAINT uq_nbp_currency_date UNIQUE (currency, effective_date);
CREATE INDEX ix_nbp_rates_currency       ON nbp_rates (currency);
CREATE INDEX ix_nbp_rates_effective_date ON nbp_rates (effective_date);
```

| # | Поле | Тип | Ограничения | Описание |
|---|------|-----|-------------|----------|
| 1 | `id` | `INTEGER` | `PK` | Идентификатор |
| 2 | `currency` | `CHAR(3)` | `NOT NULL`, индекс | Код валюты (ISO 4217) |
| 3 | `effective_date` | `DATE` | `NOT NULL`, индекс | Дата действия курса |
| 4 | `rate_pln` | `NUMERIC(10,4)` | `NOT NULL` | Курс к PLN |
| 5 | `table_no` | `VARCHAR(20)` | nullable | Номер таблицы НБП |
| 6 | `fetched_at` | `TIMESTAMPTZ` | `NOT NULL` | Момент загрузки из API |

---

## 14. Таблица `country_rate_snapshots` — Снимки секторных ставок

Замороженный слепок того, КАКАЯ ставка из КАКОГО YAML-периода использовалась в конкретном расчёте ЗП. Гарантирует воспроизводимость при последующем обновлении YAML. **Append-only**, мутации запрещены.

### 14.1. DDL

```sql
CREATE TABLE country_rate_snapshots (
    id                 SERIAL        PRIMARY KEY,
    country            CHAR(2)       NOT NULL,
    rate_name          VARCHAR(64)   NOT NULL,
    queried_for_date   DATE          NOT NULL,
    hourly             NUMERIC(10,4) NOT NULL,
    monthly_gross      NUMERIC(10,2),
    currency           CHAR(3)       NOT NULL,
    period_valid_from  DATE          NOT NULL,
    period_valid_to    DATE,
    period_verified_at DATE          NOT NULL,
    period_verified_by VARCHAR(64)   NOT NULL,
    created_at         TIMESTAMPTZ   NOT NULL
);

CREATE INDEX ix_country_rate_snapshots_country ON country_rate_snapshots (country);
```

| # | Поле | Тип | Описание |
|---|------|-----|----------|
| 1 | `id` | `INTEGER` | PK |
| 2 | `country` | `CHAR(2)` | Страна (индекс) |
| 3 | `rate_name` | `VARCHAR(64)` | Имя ставки |
| 4 | `queried_for_date` | `DATE` | Дата, на которую искалась ставка |
| 5 | `hourly` | `NUMERIC(10,4)` | Найденная часовая ставка |
| 6 | `monthly_gross` | `NUMERIC(10,2)` | Месячная брутто-ставка (опц.) |
| 7 | `currency` | `CHAR(3)` | Валюта ставки |
| 8 | `period_valid_from` | `DATE` | Начало периода действия YAML-ставки |
| 9 | `period_valid_to` | `DATE` | Конец периода (опц.) |
| 10 | `period_verified_at` | `DATE` | Когда ставка была верифицирована |
| 11 | `period_verified_by` | `VARCHAR(64)` | Кем верифицирована |
| 12 | `created_at` | `TIMESTAMPTZ` | Когда создан снимок (= когда выполнялся расчёт) |

---

## 15. Связи между таблицами (ER)

```
organisation (1) ──< drivers              (organisation_uuid, RESTRICT)
organisation (1) ──< vehicles             (organisation_uuid, RESTRICT)

drivers (1) ──< driver_contracts          (driver_id,  CASCADE)
drivers (1) ──< driver_document           (driver_uuid, RESTRICT)
drivers (1) ──< trips                     (driver_id,  RESTRICT)
drivers (1) ──< payroll_periods*          (driver_id,  RESTRICT)

driver_document (1) ──< driver_file       (document_uuid, CASCADE)

vehicles (1) ──< vehicle_document         (vehicle_uuid, RESTRICT)
vehicles (1) ──< trips                    (vehicle_id,  SET NULL, nullable)

document_type (1) ──< driver_document     (document_type+entity_type, RESTRICT)
document_type (1) ──< vehicle_document    (document_type+entity_type, RESTRICT)

trips (1) ──< trip_segments               (trip_id, CASCADE)

nbp_rates (1) ──< payroll_periods*        (nbp_rate_id, SET NULL)
country_rate_snapshots (1) ──< payroll_lines*  (snapshot_id, SET NULL)
payroll_periods* (1) ──< payroll_lines*   (period_id, CASCADE)
```

\* Таблицы `payroll_periods` / `payroll_lines` описаны в Приложении A — они реализованы в коде, но намеренно исключены из метаданных и миграций до завершения системы документов.

---

## 16. Изменения относительно версии 1.0

| # | Область | Было (v1.0) | Стало (v2.0) |
|---|---------|-------------|--------------|
| 1 | Имена таблиц | `driver`, `vehicle` | `drivers`, `vehicles` (мн. число) |
| 2 | Поля водителя | `name`, `surname` | `first_name`, `last_name` |
| 3 | Водитель: новые колонки | `pesel`/`phone`/`notes` в `extra` | вынесены в колонки + добавлены `passport_number`, `tachograph_card_number`, `hire_date`, `termination_date`, `is_active` |
| 4 | Документы водителя: файлы | `file_link TEXT` (одна ссылка) → `file_links JSONB` (массив) | отдельная таблица `driver_file` (1-ко-многим, своя мета у файла); `vehicle_document.file_links` пока сохранён |
| 5 | Каталог типов | хардкод-перечень в коде | таблица `document_type` (операторо-редактируемая) + `entity_type`-дискриминатор и составной FK |
| 6 | Новые сущности | — | `users`, `driver_contracts`, `trips`, `trip_segments`, `nbp_rates`, `country_rate_snapshots` (+ планируемые `payroll_*`) |
| 7 | UUID / JSONB | только Postgres | портируемые типы: Postgres (prod) + SQLite (тесты) |
| 8 | `updated_at` через триггер | DB-триггер `set_updated_at()` | ORM `onupdate` (паритет с SQLite) |
| 9 | ТС в схеме | вынесено в отдельное ТЗ | включено (`vehicles`, `vehicle_document`) |

---

## 17. Принятые решения

| # | Вопрос | Решение |
|---|--------|---------|
| 1 | Справочник типов документов отдельной таблицей? | ✅ Да — добавлена таблица `document_type` (v2.0), составной FK из документов |
| 2 | Хранить несколько сканов на документ? | ✅ Да — для водителя отдельная таблица `driver_file` (1-ко-многим), у каждого файла своя распознанная мета (§8a); `vehicle_document` пока хранит массив `file_links JSONB` |
| 3 | Договоры водителя в схеме? | ✅ Да — таблица `driver_contracts` (история, один-ко-многим) |
| 4 | Учёт рейсов и Mobility Package? | ✅ Да — `trips` + `trip_segments` с классификацией сегментов |
| 5 | Воспроизводимость расчётов ЗП? | ✅ Да — кэш `nbp_rates` и append-only `country_rate_snapshots` |
| 6 | Поддержка SQLite для тестов? | ✅ Да — портируемые `UuidType`/`JsonB`, ORM-`onupdate` вместо триггеров |

---

## 18. Следующие этапы

| Этап | Содержание |
|------|-----------|
| **Документы организаций** | Реализация `organisation_document` (каталог уже поддерживает `entity_type = organisation`) |
| **Payroll** | Включение `payroll_periods` / `payroll_lines` в метаданные и миграции (Приложение A) |
| **Phase 4** | Автогенерация `trip_segments` из тахографа + GPS |

---

## Приложение A. Таблицы расчёта ЗП (`payroll_*`) — запланировано

Модели `PayrollPeriod` и `PayrollLine` реализованы в `app/payroll/models.py`, но **намеренно исключены** из `app/models/__init__.py` — их таблицы пока не входят в метаданные и миграции, пока достраивается система документов. Приведено для полноты.

### A.1. `payroll_periods`

Расчётный период по водителю за месяц. Уникальность `(driver_id, year, month)`.

Ключевые поля: `id` (PK), `driver_id` (FK → drivers, RESTRICT), `year`, `month`, `status` (`draft`/`calculated`/`approved`/`paid`), `eur_pln_rate` `NUMERIC(10,4)`, `nbp_rate_id` (FK → nbp_rates, SET NULL), `days_abroad_auto`, `days_abroad_override`, денормализованные итоги (`total_gross_pln`, `foreign_wage_pln`, `equalization_pln`, `zus_base_pln`, `pit_base_pln`, `sanitariaty_pln`, `zus_employee_pln`, `zdrowotne_pln`, `pit_advance_pln`, `total_net_pln`), `created_at`, `calculated_at`, `calculator_version`.

### A.2. `payroll_lines`

Строки расчёта. `id` (PK), `period_id` (FK → payroll_periods, CASCADE), `line_type` (enum), `country`, `hours`, `rate_hourly_native`, `rate_currency`, `amount_native`, `amount_pln` (NOT NULL), `snapshot_id` (FK → country_rate_snapshots, SET NULL), `description`.

Типы строк (`PayrollLineType`): `base_salary`, `foreign_wage`, `equalization`, `virtual_diet_zus`, `virtual_diet_pit`, `sanitariaty`, `zus_employee`, `zdrowotne`, `pit_advance`.
