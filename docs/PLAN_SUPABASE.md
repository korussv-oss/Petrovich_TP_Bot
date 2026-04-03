# План подключения Supabase вместо JSON-хранилища (через этап SQLite)

Коллега рекомендует заменить файловое хранение (JSON) на Supabase (PostgreSQL + REST/Realtime API).

**Актуальный статус проекта:** сейчас реализовано локальное хранилище **SQLite** (вместо JSON). Supabase остаётся следующим этапом, если понадобится масштабирование (несколько процессов/серверов) и единый центральный источник данных.

Ниже — план перехода на Supabase **без изменения поведения для пользователей** и с минимальными рисками (через переключатель в `.env`).

---

## 1. Зачем Supabase

- **Один источник правды**: база вместо нескольких JSON-файлов.
- **Конкурентный доступ**: блокировок файлов нет, удобно при нескольких воркерах/репликах.
- **Бэкапы и восстановление**: встроенные в Supabase + возможность своих снимков.
- **Масштаб**: индексы, запросы по полям, фильтрация без загрузки всего файла в память.
- **Без своего сервера БД**: хостинг и обновления на стороне Supabase.

---

## 2. Текущее JSON-хранилище (что переносим)

| Файл | Назначение | Модуль |
|------|------------|--------|
| `data/user_data.json` | Профили: `telegram_id` → { full_name, login, email, phone, department, department_wms, employee_id, … } | `user_storage.py` |
| `data/index_by_login.json` | Логин → telegram_id (уникальность) | `user_storage.py` |
| `data/index_by_email.json` | Email → telegram_id | `user_storage.py` |
| `data/index_by_phone.json` | Нормализованный телефон → telegram_id (привязка MAX) | `user_storage.py` |
| `data/index_by_employee_id.json` | Табельный номер → telegram_id | `user_storage.py` |
| `data/index_by_max_user.json` | max_user_id → telegram_id (связка MAX↔TG) | `user_storage.py` |
| `data/issue_binding_registry.json` | Реестр привязок: channel_id, channel_user_id, issue_key, project_key, ticket_type_id, created_at | `core/support/issue_binding_registry.py` |
| `data/issue_notification_state.json` | Состояние уведомлений: issue_key → last_status, last_comment_count | `core/notifications.py` |
| `data/pending_password_requests.json` | Ожидающие смену пароля: issue_key → { user_id, channel_id } | `core/password_requests.py` |
| `data/wms_departments_cache.json` | Кэш подразделений WMS из Jira | `core/jira_wms_departments.py` |
| `data/departments_cache.json` | Кэш подразделений AA из Jira | `core/jira_departments.py` |

Кэши (wms_departments, departments) можно оставить в JSON или тоже перенести в Supabase — по желанию.

---

## 2.1. Что уже сделано: SQLite вместо JSON

Сейчас данные хранятся в файле SQLite:

- `data/storage.sqlite3`

Переключатель (в `.env`):

```env
USE_SQLITE_STORAGE=1
# SQLITE_PATH=data/storage.sqlite3   # опционально: если нужен другой путь
```

Миграция данных из старых JSON выполняется один раз:

```bash
python scripts/migrate_json_to_sqlite.py
```

После этого бот может работать полностью на SQLite, а старые JSON можно оставить как резерв до завершения проверки.

---

## 3. Схема Supabase (таблицы)

### 3.1. Пользователи и индексы

**Таблица `profiles`**

- `id` (uuid, PK, default gen_random_uuid()) — внутренний id.
- `channel_id` (text) — `"telegram"` или `"max"`.
- `channel_user_id` (bigint) — telegram_id или max user_id.
- `full_name`, `login`, `email`, `phone` (text, nullable).
- `department`, `department_wms`, `employee_id` (text, nullable).
- `created_at`, `updated_at` (timestamptz).

Уникальный ключ: `(channel_id, channel_user_id)`.  
Индексы: по `login`, `email`, нормализованному `phone`, `employee_id` (уникальные при необходимости), по `channel_id, channel_user_id`.

**Таблица `channel_links`** (привязка MAX ↔ Telegram)

- `id` (uuid, PK).
- `telegram_id` (bigint), `max_user_id` (bigint), `created_at`.
- Уникальность по `telegram_id` и по `max_user_id`.

Либо один профиль на канал и отдельная таблица связей, либо один профиль с `telegram_id` и опциональным `max_user_id` — зависит от текущей модели (сейчас в коде один профиль на пару channel_id + user_id и отдельные индексы).

**Рекомендация:** одна таблица `profiles` с полями `channel_id`, `channel_user_id`; отдельная таблица `channel_links(telegram_id, max_user_id)` для поиска «по max_user_id получить telegram_id». Индексы в БД заменяют текущие index_*.json.

### 3.2. Реестр привязок заявок

**Таблица `issue_bindings`**

- `id` (uuid, PK).
- `channel_id` (text), `channel_user_id` (bigint), `issue_key` (text), `project_key` (text), `ticket_type_id` (text), `created_at` (timestamptz).
- Уникальность: `(issue_key, channel_id, channel_user_id)`.
- Индекс по `issue_key` (для уведомлений), по `(channel_id, channel_user_id)` (для «Мои заявки»).

### 3.3. Уведомления и смена пароля

**Таблица `issue_notification_state`**

- `issue_key` (text, PK).
- `last_status` (text), `last_comment_count` (int), `updated_at` (timestamptz).

**Таблица `pending_password_requests`**

- `issue_key` (text, PK).
- `user_id` (bigint), `channel_id` (text), `created_at` (timestamptz).

Кэши отделов (если переносим):

- `wms_departments_cache` (key, value jsonb, updated_at).
- `departments_cache` (key, value jsonb, updated_at).

---

## 4. Переменные окружения

В `.env` добавить (значения из дашборда Supabase):

```env
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...   # для серверного доступа (полные права)
# либо для ограниченных прав:
# SUPABASE_ANON_KEY=eyJ...
```

Также остаётся переключатель SQLite (на время перехода удобно иметь оба варианта):

```env
USE_SQLITE_STORAGE=1
SQLITE_PATH=data/storage.sqlite3
```

### 4.1. Локальный Supabase (развёртывание на своей машине)

Supabase можно поднять локально через **Docker** — те же API и PostgreSQL, без облака. Удобно для разработки и тестов без выхода в интернет.

**Требования:** установленные Docker и Docker Compose (и опционально [Supabase CLI](https://supabase.com/docs/guides/cli)).

**Вариант A: через Supabase CLI (рекомендуется)**

```bash
# Установка CLI (один раз)
# Windows: scoop install supabase  или  npm i -g supabase
# macOS: brew install supabase/tap/supabase

# В папке проекта (или в отдельной папке для инфраструктуры)
supabase init
supabase start
```

После `supabase start` в консоли появятся:
- **API URL** (обычно `http://127.0.0.1:54321`);
- **anon key** и **service_role key** для подключения.

Остановка: `supabase stop`. Сброс данных: `supabase stop --no-backup` и снова `supabase start`.

**Вариант B: только Docker Compose**

В репозитории Supabase есть [docker-compose для локального запуска](https://github.com/supabase/supabase/blob/master/docker/docker-compose.yml). Можно склонировать папку `docker` и запустить:

```bash
git clone --depth 1 https://github.com/supabase/supabase
cd supabase/docker
cp .env.example .env
docker compose up -d
```

Порты по умолчанию: 54321 (API), 5432 (PostgreSQL). Ключи сгенерируются при первом старте (см. логи или `.env` в образе).

**Переменные для бота при локальном Supabase**

В `.env` для разработки:

```env
USE_SUPABASE=1
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_SERVICE_ROLE_KEY=<service_role key из вывода supabase start или из docker>
```

Для доступа с другой машины в сети заменить `127.0.0.1` на IP хоста (и при необходимости настроить порты в docker-compose).

**Что важно при локальном запуске**

1. **Схема и миграции:** таблицы (раздел 3 плана) нужно создать вручную: выполнить SQL в Studio (открывается по `http://127.0.0.1:54323` после `supabase start`) или через миграции CLI (`supabase migration new ...` и `supabase db push`).
2. **Данные:** при перезапуске контейнеров с `supabase stop` без `--no-backup` данные сохраняются в томах Docker. Для «чистого» старта — `supabase stop --no-backup` и снова `supabase start`.
3. **Прод:** для продакшена обычно используют облачный проект Supabase; локальный вариант — для dev/test и для полностью внутреннего развёртывания (свой сервер с Docker).

Итог: один и тот же код бота работает и с облачным, и с локальным Supabase; меняются только `SUPABASE_URL` и ключ в `.env`.

**Флаг переключения хранилища**

```env
USE_SUPABASE=1
```

При `USE_SUPABASE=0` или отсутствии переменной — использовать текущее JSON-хранилище.

---

## 5. Зависимости

```bash
pip install supabase
```

В `requirements.txt`:

```
supabase>=2.0.0
```

Проверить совместимость с Python 3.10+.

---

## 6. План реализации (по шагам)

### Шаг 1. Supabase-проект и таблицы

1. Создать проект на [supabase.com](https://supabase.com).
2. В SQL Editor выполнить DDL:
   - создание таблиц `profiles`, `channel_links`, `issue_bindings`, `issue_notification_state`, `pending_password_requests` (и при необходимости кэшей);
   - уникальные ограничения и индексы;
   - RLS (Row Level Security): при использовании `service_role` ключа RLS можно не включать или оставить политики «разрешить всё для service_role».
3. Сохранить `SUPABASE_URL` и `SUPABASE_SERVICE_ROLE_KEY` в `.env`.

### Шаг 2. Слой доступа к данным (абстракция)

Ввести тонкий слой так, чтобы остальной код не знал, JSON или Supabase:

- **Вариант A (рекомендуется):** один модуль `core/storage/__init__.py` (или `storage_backend.py`) с функциями того же контракта, что и сейчас:
  - профили: `get_user_profile(channel_id, user_id)`, `save_user_profile(channel_id, user_id, profile)`, `get_telegram_id_by_phone(phone)`, `get_telegram_id_by_max_user(max_user_id)`, проверки дубликатов по login/email/employee_id и т.д.;
  - реестр: `add_binding(...)`, `get_bindings_by_user(...)`, `get_user_ids_by_issue(...)` и остальные из `issue_binding_registry`;
  - уведомления: get/set last_status, last_comment_count по issue_key;
  - pending password: add/get/remove по issue_key.

Внутри слоя:

- если `USE_SUPABASE` — вызывать реализацию через Supabase (отдельный модуль `core/storage/supabase_backend.py`);
- иначе — вызывать текущие функции из `user_storage`, `issue_binding_registry`, `notifications`, `password_requests` (обёртки над JSON).

Тогда `user_storage.py`, `issue_binding_registry.py`, логика в `notifications.py` и `password_requests.py` по очереди переключаются на вызов этого слоя; при этом сигнатуры публичных функций остаются теми же.

---

## 6.8. Переход SQLite → Supabase (как сделать позже, без переписывания бота)

Идея: **не трогать бизнес-логику**, а заменить только “куда читаем/пишем данные”.

### Шаг A. Поднять Supabase

- **Dev (Windows):** поднять локально через Docker/CLI (раздел 4.1)
- **Prod (Linux):** облачный Supabase или собственный Postgres/Supabase внутри компании

### Шаг B. Создать таблицы в Supabase

Выполнить DDL из раздела 3 (profiles, channel_links, issue_bindings, issue_notification_state, pending_password_requests).

### Шаг C. Добавить Supabase backend параллельно SQLite

Сделать модуль `core/storage/supabase_backend.py` с теми же операциями, что уже есть для SQLite:

- профили пользователей
- привязки MAX↔TG
- реестр issue_bindings
- issue_notification_state
- pending_password_requests

Важно: **контракт функций должен быть одинаковый**, чтобы переключение было только через `.env`.

### Шаг D. Скрипт миграции SQLite → Supabase

Написать `scripts/migrate_sqlite_to_supabase.py`:

1. Читать данные из `data/storage.sqlite3`
2. Вставить/обновить строки в Supabase (upsert)
3. Проверить количества записей и несколько выборочных пользователей/привязок

### Шаг E. Переключить хранилище флагом

В `.env` на сервере:

```env
USE_SUPABASE=1
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...

# На всякий случай оставить рядом (для отката)
USE_SQLITE_STORAGE=0
SQLITE_PATH=data/storage.sqlite3
```

### Шаг F. План отката

Если что-то пошло не так — вернуть:

```env
USE_SUPABASE=0
USE_SQLITE_STORAGE=1
```

И бот продолжит работать на локальной базе SQLite без потери работоспособности.

### Шаг 3. Реализация бэкенда Supabase

- **Файл** `core/storage/supabase_client.py`: инициализация клиента Supabase (чтение `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`), ленивое создание клиента (один раз на процесс).
- **Файл** `core/storage/supabase_backend.py`: реализация всех операций из п. 6.2 поверх таблиц из п. 3:
  - профили: select/upsert по (channel_id, channel_user_id); отдельные запросы для индексов (по login, email, phone, employee_id, max_user_id).
  - реестр: insert с проверкой дубликата, select по issue_key и по (channel_id, channel_user_id).
  - notification state: upsert по issue_key.
  - pending_password: upsert/delete по issue_key.

Важно сохранить текущую семантику (например, нормализацию телефона, уникальность логина/email в рамках одного канала и т.д.).

### Шаг 4. Подключение шифрования (если используется)

Сейчас в `user_storage` при `ENCRYPT_USER_DATA=1` поля full_name, login, email, phone шифруются перед записью в JSON. В Supabase можно:

- либо шифровать перед записью и расшифровывать после чтения (как сейчас), храня в БД уже зашифрованные строки;
- либо положиться на шифрование Supabase (at-rest) и не хранить чувствительные поля в открытом виде в логах.

Минимальные изменения: те же `_encrypt_value`/`_decrypt_value` вызывать в `supabase_backend` при записи/чтении соответствующих полей профиля.

### Шаг 5. Переключение модулей на слой хранилища

- Заменить прямые вызовы `user_storage` на вызовы из `core/storage` (или оставить в `user_storage` обёртку: если USE_SUPABASE — вызывать supabase_backend, иначе текущую логику). То же для `issue_binding_registry`, для `notifications` (get/set last_status, last_comment_count), для `password_requests` (add_pending, get_all_pending, remove_pending).
- Прогнать тесты и ручные сценарии: регистрация, привязка по телефону/MAX, создание заявок, «Мои заявки», уведомления о статусе и комментариях, смена пароля.

### Шаг 6. Миграция существующих данных из JSON в Supabase

Один раз перед переключением на Supabase:

1. Скрипт (например `scripts/migrate_json_to_supabase.py`):
   - читает `user_data.json` и индексы → вставляет/обновляет `profiles` и при необходимости `channel_links`;
   - читает `issue_binding_registry.json` → вставляет в `issue_bindings`;
   - читает `issue_notification_state.json` → вставляет в `issue_notification_state`;
   - читает `pending_password_requests.json` → вставляет в `pending_password_requests`.
2. Проверка: сравнить количество записей, выборочно сравнить данные.
3. Включить `USE_SUPABASE=1` и прогнать бота в тестовом окружении.

Кэши (wms_departments, departments) при первой загрузке из Jira заполнятся заново; при желании их тоже можно перенести скриптом.

### Шаг 7. Кэши отделов (опционально)

Если решите хранить кэши в Supabase: в `jira_wms_departments.py` и `jira_departments.py` заменить чтение/запись JSON на вызовы слоя хранилища с бэкендом Supabase (одна таблица с key/value или две таблицы под каждый кэш). TTL можно реализовать по полю `updated_at`.

---

## 7. Порядок работ (кратко)

1. Создать проект Supabase, накидать таблицы и индексы.
2. Добавить `supabase` в зависимости и переменные в `.env`.
3. Реализовать `core/storage/supabase_client.py` и `core/storage/supabase_backend.py` с тем же контрактом, что и текущие модули.
4. Ввести общий слой `core/storage` и переключение по `USE_SUPABASE`.
5. Перевести на слой: user_storage → issue_binding_registry → notifications (state) → password_requests.
6. Написать и выполнить скрипт миграции JSON → Supabase.
7. Включить Supabase в тесте, затем в проде; при проблемах — откат через `USE_SUPABASE=0`.

---

## 8. Риски и откат

- **Сетевые ошибки:** при недоступности Supabase логировать ошибку и при необходимости падать в текущем запросе; можно добавить retry с ограничением. Критичные пути (регистрация, создание заявки) лучше покрыть тестами.
- **Откат:** оставить код работы с JSON; переключение только через `USE_SUPABASE`. В случае сбоев вернуть `USE_SUPABASE=0` и откатить деплой.

После выполнения плана все данные, которые сейчас лежат в перечисленных JSON, будут храниться в Supabase, а уведомления в ТГ и МАХ продолжат работать через текущую доставку без изменения формата сообщений.
