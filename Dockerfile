FROM python:3.11-slim

# Не буферизовать вывод и не создавать .pyc-файлы
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow \
    DEBIAN_FRONTEND=noninteractive

# Рабочая директория внутри контейнера
WORKDIR /app

# Системные зависимости (tzdata для часового пояса, build-essential/libssl-dev/libffi-dev — для криптографии и сетевых библиотек)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    build-essential \
    libssl-dev \
    libffi-dev \
 && rm -rf /var/lib/apt/lists/*

# Отдельным слоем копируем только зависимости Python
COPY requirements.txt .

# Устанавливаем зависимости без кеша
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код бота
COPY . .

# Каталог для логов и данных бота (профили, реестры, кэши)
RUN mkdir -p /app/data

# Опционально объявляем том для данных (можно монтировать в docker run / docker-compose)
VOLUME ["/app/data"]

# Проверка "живости": бот создаёт файл-замок при запуске — его наличие == процесс работает
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import os, sys; sys.exit(0 if os.path.exists('/app/data/rubik_singleton.lock') else 1)"

# Команда запуска: Telegram-бот + (при наличии MAX_BOT_TOKEN) MAX-бот в одном процессе
CMD ["python", "main.py"]

