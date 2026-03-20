FROM ubuntu:22.04

# Обновить пакеты и установить Python
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Назначить Python по умолчанию
RUN ln -s /usr/bin/python3.10 /usr/bin/python

# Настроить рабочую директорию
WORKDIR /workspace

# Ограничения окружения
ENV PYTHONUNBUFFERED=1

# Участникам нужно примонтировать исходный код в /workspace
# и запускать контейнер так: docker run --network none -v $(pwd):/workspace <image>
