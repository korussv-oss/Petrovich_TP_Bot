"""
JSON-backed FSM storage для Aiogram 3.

Заменяет MemoryStorage, который теряет все незавершённые FSM-сессии при перезапуске бота.
При использовании JsonFsmStorage пользователь, заполнявший форму (создание тикета,
регистрация), после перезапуска бота продолжает с того же места.

Файл: data/fsm_state.json
Запись — атомарная (tmp + os.replace), чтобы сбой не привёл к порче файла.
Без внешних зависимостей (не требует Redis, SQLite или доп. пакетов).

Ограничение: хранилище не масштабируется горизонтально (один процесс).
Для multi-worker деплоя следует использовать RedisStorage.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data/fsm_state.json")


class JsonFsmStorage(BaseStorage):
    """
    Персистентное FSM-хранилище на основе JSON-файла.
    Данные загружаются при старте, изменения сразу записываются на диск.
    """

    def __init__(self, path: str | Path = _DEFAULT_PATH):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = self._load_from_disk()

    def _load_from_disk(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("JsonFsmStorage: ошибка чтения %s: %s — стартуем с пустым хранилищем", self._path, e)
        return {}

    def _flush(self) -> None:
        """Атомарная запись на диск: tmp-файл + rename."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self._path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(self._path))
        except Exception as e:
            logger.warning("JsonFsmStorage: ошибка записи: %s", e)

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        async with self._lock:
            k = self._key(key)
            entry = self._data.setdefault(k, {})
            if state is None:
                entry.pop("state", None)
            else:
                entry["state"] = state.state if hasattr(state, "state") else str(state)
            self._flush()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        k = self._key(key)
        return self._data.get(k, {}).get("state")

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self._lock:
            k = self._key(key)
            entry = self._data.setdefault(k, {})
            entry["data"] = dict(data)
            self._flush()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        k = self._key(key)
        return dict(self._data.get(k, {}).get("data", {}))

    async def close(self) -> None:
        pass
