import requests, json, re
import os
import argparse
#❗❗❗эти параметры надо изменять (остальное не трогать!)--------------------❗❗❗
INPUT_FILE = "iceberg.csv" # входной CSV-файл с ключевыми словами
GROUPS_START = 0 # с какой группы начать (индексация с 0)
GROUPS_END = 1 # до какой группы (не включительно), None — до конца
#❗❗❗эти параметры надо изменять (остальное не трогать!)--------------------❗❗❗

#---------
def parse_cli_params():
    parser = argparse.ArgumentParser(
        description="Клиент для stream-обработки статей (без правки кода)."
    )
    parser.add_argument(
        "-i", "--input-file",
        default=os.getenv("INPUT_FILE", INPUT_FILE),
        help='Путь к CSV (по умолчанию берётся из переменной окружения INPUT_FILE или "iceberg.csv").'
    )
    parser.add_argument(
        "-s", "--start",
        type=int,
        default=int(os.getenv("GROUPS_START", GROUPS_START)),
        help=f"Номер начальной группы (0-индексация). По умолчанию: {GROUPS_START}."
    )
    parser.add_argument(
        "-e", "--end",
        default=os.getenv("GROUPS_END", str(GROUPS_END) if GROUPS_END is not None else "None"),
        help="Номер конечной группы (не включительно). Укажи число или 'None' — до конца."
    )
    args = parser.parse_args()

    # Преобразуем end: строка 'None' -> None, иначе int
    end_val = None if str(args.end).lower() == "none" else int(args.end)

    return args.input_file, args.start, end_val

# Используем финальные параметры по всему скрипту:
INPUT_FILE, GROUPS_START, GROUPS_END = parse_cli_params()
#---------




SAVE_HTML = False
KEEP_SERVER_COPY = True  # важно!

API_STREAM = "http://62.197.49.99:8001/articles_generator_stream_upload"
API_DOWNLOAD = "http://62.197.49.99:8001/download_once"

files = {"file": (INPUT_FILE, open(INPUT_FILE, "rb"), "text/csv")}
data = {
    "groups_start": str(GROUPS_START),
    "save_html": str(SAVE_HTML).lower(),
    "keep_server_copy": str(KEEP_SERVER_COPY).lower(),
}
if GROUPS_END is not None:
    data["groups_end"] = str(GROUPS_END)

articles_csv_path = None

with requests.post(API_STREAM, files=files, data=data, stream=True, timeout=3600) as r:
    r.raise_for_status()
    for raw in r.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8")
        if line.startswith("data: "):
            payload = line[6:]
            print(payload)  # печатаем логи как есть
            # ловим финальную строку с результатом
            if payload.startswith("{"):
                try:
                    obj = json.loads(payload)
                    if "_result" in obj and "articles_csv" in obj["_result"]:
                        articles_csv_path = obj["_result"]["articles_csv"]
                except json.JSONDecodeError:
                    pass

if not articles_csv_path:
    raise SystemExit("Не получил путь к файлу из стрима (_result.articles_csv).")

# Скачиваем файл отдельным GET
resp = requests.get(API_DOWNLOAD, params={"path": articles_csv_path}, timeout=3600)
resp.raise_for_status()
with open("articles.csv", "wb") as f:
    f.write(resp.content)

print("✅ Сохранено: articles.csv")