import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict
import aiohttp

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", ".")
DATA_FILE = os.path.join(DATA_DIR, "stations.json")

METAR_API = "https://aviationweather.gov/api/data/metar"


class WeatherMonitor:
    def __init__(self):
        self.stations: Dict[str, List[Dict]] = {}
        self._load()

    def _load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    self.stations = json.load(f)
                logger.info(f"Loaded {self.get_total_stations()} stations")
            except Exception as e:
                logger.error(f"Failed to load stations: {e}")
                self.stations = {}

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DATA_FILE, "w") as f:
                json.dump(self.stations, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save: {e}")

    def add_station(self, chat_id: str, code: str) -> str:
        code = code.upper()
        if chat_id not in self.stations:
            self.stations[chat_id] = []
        for s in self.stations[chat_id]:
            if s["code"] == code:
                return "exists"
        self.stations[chat_id].append({
            "code": code,
            "last_temp_f": None,
            "last_temp_c": None,
            "last_time": None,
        })
        self._save()
        return "added"

    def remove_station(self, chat_id: str, code: str):
        code = code.upper()
        if chat_id in self.stations:
            self.stations[chat_id] = [
                s for s in self.stations[chat_id] if s["code"] != code
            ]
            self._save()

    def get_stations(self, chat_id: str) -> List[Dict]:
        return self.stations.get(chat_id, [])

    def get_total_stations(self) -> int:
        return sum(len(v) for v in self.stations.values())

    def _update_temp(self, chat_id: str, code: str, temp_f: float, temp_c: float, time_str: str):
        if chat_id in self.stations:
            for s in self.stations[chat_id]:
                if s["code"] == code:
                    s["last_temp_f"] = temp_f
                    s["last_temp_c"] = temp_c
                    s["last_time"] = time_str
        self._save()

    def _parse_metar_temp(self, raw: str) -> Optional[Tuple[float, float]]:
        """
        Парсить температуру з METAR рядка.
        Формат: TT/DD де TT=температура, DD=точка роси
        Також підтримує T01720094 формат для точніших даних.
        """
        # Спочатку пробуємо точний формат T01720094
        # T + 4 цифри температури + 4 цифри точки роси
        t_match = re.search(r'T(\d{4})(\d{4})', raw)
        if t_match:
            temp_raw = t_match.group(1)
            # Перший біт = знак (0=плюс, 1=мінус)
            sign = -1 if temp_raw[0] == '1' else 1
            temp_c = sign * int(temp_raw[1:]) / 10.0
            temp_f = temp_c * 9/5 + 32
            return temp_f, temp_c

        # Стандартний формат TT/DD
        temp_match = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
        if temp_match:
            temp_str = temp_match.group(1)
            sign = -1 if temp_str.startswith('M') else 1
            temp_c = sign * int(temp_str.replace('M', ''))
            temp_f = temp_c * 9/5 + 32
            return temp_f, temp_c

        return None

    def _parse_metar_time(self, raw: str) -> str:
        """Парсить час з METAR."""
        time_match = re.search(r'\d{6}Z', raw)
        if time_match:
            t = time_match.group(0)
            day = t[:2]
            hour = t[2:4]
            minute = t[4:6]
            return f"{hour}:{minute} UTC"
        return ""

    async def fetch_metar(self, station: str) -> Optional[Dict]:
        """Отримує поточний METAR для станції."""
        station = station.upper()
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "ids": station,
                    "format": "raw",
                    "taf": "false",
                    "hours": "1",
                }
                async with session.get(
                    METAR_API,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"METAR API {resp.status} for {station}")
                        return None

                    text = await resp.text()
                    if not text.strip():
                        return None

                    # Беремо перший рядок з METAR
                    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
                    metar_line = None
                    for line in lines:
                        if station in line:
                            metar_line = line
                            break

                    if not metar_line:
                        metar_line = lines[0] if lines else ""

                    if not metar_line:
                        return None

                    result = self._parse_metar_temp(metar_line)
                    if result is None:
                        return None

                    temp_f, temp_c = result
                    time_str = self._parse_metar_time(metar_line)

                    return {
                        "temp_f": temp_f,
                        "temp_c": temp_c,
                        "time": time_str,
                        "raw": metar_line,
                    }

        except Exception as e:
            logger.error(f"Error fetching METAR for {station}: {e}")
            return None

    async def check_temperature_changes(self) -> List[Tuple]:
        """Перевіряє всі станції на зміну температури."""
        if not self.stations:
            return []

        notifications = []

        # Збираємо унікальні станції
        unique_stations = {}
        for chat_id, station_list in self.stations.items():
            for s in station_list:
                code = s["code"]
                if code not in unique_stations:
                    unique_stations[code] = []
                unique_stations[code].append((chat_id, s))

        # Запитуємо всі станції
        for code, subscribers in unique_stations.items():
            data = await self.fetch_metar(code)
            if data is None:
                await asyncio.sleep(0.5)
                continue

            new_f = data["temp_f"]
            new_c = data["temp_c"]
            time_str = data["time"]

            for chat_id, station_data in subscribers:
                old_f = station_data.get("last_temp_f")

                # Перша перевірка — просто зберігаємо
                if old_f is None:
                    self._update_temp(chat_id, code, new_f, new_c, time_str)
                    continue

                # Перевіряємо чи змінилась температура (порівнюємо до 1 знаку)
                if abs(new_f - old_f) >= 0.5:
                    old_c = station_data.get("last_temp_c", 0)
                    notifications.append((chat_id, code, old_f, new_f, old_c, new_c, time_str))
                    self._update_temp(chat_id, code, new_f, new_c, time_str)
                    logger.info(f"Temp change {code}: {old_f:.1f}°F → {new_f:.1f}°F")

            await asyncio.sleep(0.3)

        return notifications
