import requests

# Основные параметры
API_URL = "http://62.197.49.99:8001/articles_generator_upload"
INPUT_FILE = "iceberg.csv"   # входной CSV-файл с ключевыми словами
OUTPUT_FILE = "articles.csv" # куда сохранить результат
GROUPS_START = 0             # с какой группы начать (индексация с 0)
GROUPS_END = 1               # до какой группы (не включительно), None — до конца
SAVE_HTML = False            # сохранять ли отдельные HTML-файлы
KEEP_SERVER_COPY = False     # оставлять ли итоговый файл на сервере
TIMEOUT = 3600               # таймаут на запрос в секундах

# Формируем данные для POST-запроса
files = {"file": (INPUT_FILE, open(INPUT_FILE, "rb"), "text/csv")}
data = {
    "groups_start": str(GROUPS_START),
    "groups_end": str(GROUPS_END) if GROUPS_END is not None else "",
    "save_html": str(SAVE_HTML).lower(),
    "keep_server_copy": str(KEEP_SERVER_COPY).lower(),
}

# Отправляем запрос
with requests.post(API_URL, files=files, data=data, stream=True, timeout=TIMEOUT) as r:
    r.raise_for_status()

    groups = r.headers.get("X-Groups-Processed", "?")
    total = r.headers.get("X-Total-Cost", "?")

    with open(OUTPUT_FILE, "wb") as f:
        for chunk in r.iter_content(1 << 14):
            if chunk:
                f.write(chunk)

print(f"Обработано статей: {groups}. Итоговая сумма: ${total}")