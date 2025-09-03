from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional

import tiktoken
from tenacity import retry, stop_after_attempt, wait_exponential_jitter
from openai import OpenAI

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
import uvicorn
from tqdm.auto import tqdm
from fastapi.responses import StreamingResponse, FileResponse
from threading import Thread
from queue import Queue
import uuid
# ─────────────────────────────── НАСТРОЙКИ ПУТЕЙ ───────────────────────────────
# ВСЕ файлы читаем/пишем в хостовую папку (монтируемую как /work).
BASE_DIR = Path(os.getenv("HOST_WORKDIR", "/work"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────── ПРОМПТЫ ───────────────────
SYSTEM_PROMPT_TZ = (
    "Ты — автор экспертного блога о ремонте техники, совмещающий опыт мастера и журналиста. "
    "Пишешь для мастеров и обычных людей, уважительно, по делу, с опорой на официальные данные. "
    "Тон дружелюбный, но профессиональный, допускается лёгкий жаргон и бытовые примеры. "

    # Жёсткие правила заголовков
    "ВНИМАНИЕ: запрещены двоеточия, тире, скобки, союзы «и/или» в заголовках любого уровня. "
    "Один заголовок — одна мысль или один вопрос. Заголовки длиной 2–5 слов, без кликбейта. "

    # Анти-усреднение и анти-ИИ-шаблоны
    "Избегай обтекаемых формулировок («высока вероятность», «в некоторых случаях», «может быть»). "
    "Не вставляй пустые универсальные фразы («главный принцип — безопасность») без конкретных действий и данных. "
    "Разделы не обязаны быть одинаковыми по объёму — глубоко раскрывай важное, второстепенное описывай кратко. "
    "Списки делай разной длины и формы, чередуй короткие и длинные пункты. "
    "Не используй ИИ-шаблоны и симметричную структуру, избегай усреднения."
)

TZ_USER_PROMPT_TEMPLATE = textwrap.dedent(
    """
    КОНТЕКСТ
    Статья для блога про ремонт техники. Тема — "{main_query}".
    Аудитория — мастера, обычные люди люди. Цель — стать ежедневным блогом для профессионалов, реальная экспертиза, польза для обычных людей.

    РОЛЬ
    Ты — блогер, пишешь на тему ремонта техники. Пишешь коротко, по делу, уважительно. Опираешься на официальные данные.

    ИНСТРУКЦИЯ
    Разработай подробную SEO‑структуру статьи (план) с чёткой иерархией H1–H3. Распредели ключевые слова по релевантным блокам. Сделай заметки, где будут списки, таблицы, чек-листы.

    СТИЛЬ
    Язык — простой и прямой; короткие предложения; дружелюбно и с уважением; без штампов и канцелярита; немного жаргона; бытовые примеры уместны.

    TONE OF VOICE
    Перед написанием сгенерируй для "автора" случайные значения по Большой Пятёрке (экстраверсия, доброжелательность, сознательность, нейротизм, открытость опыту) пиши структуру статьи с учетом значений этих черт.

    ЖЁСТКИЕ АНТИ-ПАТТЕРНЫ
    • Не использовать «высока вероятность», «в некоторых случаях», «может быть» и другие размытые выражения.
    • Не использовать пустые универсальные фразы («главный принцип — безопасность», «следует помнить») без конкретных действий, параметров или цифр.
    • Разделы и пункты списка не должны быть одинаковой длины или структуры.
    • Чередуй короткие и длинные пункты, где-то одно слово, где-то абзац.
    • Разная глубина проработки разделов: важное раскрывать глубоко, второстепенное — кратко.
    • Избегать симметричных списков, ИИ-шаблонов и усреднённости.

    ЗАГОЛОВКИ H1-H6
    2-5 слов, одна фраза, без двоеточий, тире, скобок и вопросов. Никаких уточнений после знаков.
    Запрещено двоеточие (:), тире (—/–), скобки, союзы «и/или».

    СТРУКТУРА (разработай из подходящих для темы блоков):

    Обязательные:
    — H1: точный, понятный заголовок без кликбейта.
    — Подзаголовки H2/H3/H4: логичное разделение длинных секций внутри статьи.


    На выбор:
    — Краткое описание/лид-абзац: 1–2 емких предложения, раскрывающих суть.
    — Актуальность: “Информация актуальна на [месяц, год]”, важные изменения, нововведения.
    — Оглавление/содержание: для длинных или сложных статей — якоря.
    — Введение / Контекст / Предыстория: зачем и кому будет полезна статья, почему тема важна сейчас.
    — Факты и статистика: цифры, аналитика, тенденции, если уместно.
    — Глоссарий / пояснение терминов: для сложных/специализированных тем.
    — Пошаговая инструкция: подробное описание + нумерованный список действий.
    — Чек-лист документов/требований: список необходимых бумаг и сведений.
    — Таблицы, сравнения вариантов, иные важные табличные данные.
    — Куда обращаться: контакты, подразделения, порталы, горячие линии (без закрытых телефонов).
    — Калькулятор, формулы или схема расчёта: если требуется расчет выплат, сроков и пр.
    — Частые ошибки и как избежать: распространённые ошибки, лайфхаки по их исключению.
    — Примеры / кейсы / реальные истории: практические иллюстрации, типовые ситуации.
    — Мнение / цитата эксперта / интервью: прямые комментарии специалистов, авторская позиция.
    — Преимущества и недостатки: сравнительный анализ, плюсы-минусы вариантов (если применимо).
    — Альтернативные способы решения: если есть разные варианты/пути.
    — FAQ: минимум 5 популярных вопросов и развернутых ответов.
    — Образцы, шаблоны: документов, заявлений, договоров (если нужны).
    — Видео-/аудиоматериалы, инфографика, скриншоты: если объяснить словами сложно.
    — Подводные камни / важные нюансы / особенности: тонкости, исключения, частные случаи.
    — Дисклеймер ("Важно"): информация об актуальности, взгляд на официальные источники, напоминание о необходимости личной консультации.
    — Call to action: совет что делать дальше, куда обратиться, чем воспользоваться (если требуется по задаче).
    Используй только те блоки, которые подходят для темы и формата статьи. Остальные пропускай.
    Для каждого блока дай комментарии по написанию исходя из личности автора

    ОБЪЕМ
    Выбери объем подходящий под тему: от 3000 до 20000 знаков.
    Для каждого блока укажи примерный объём (в знаках).

    КЛЮЧЕВЫЕ ФРАЗЫ
    Используй в заголовках, подзаголовках и первых абзацах:
    {phrases_block}

    Формат ответа:
    Представь структуру статьи в виде подробного плана:
    ЯВНО ПРОПИШИ ЗАГОЛОВОК H1
    Отмечай остальные заголовки! (H2–H6)
    Для каждого блока — краткое описание содержания (про что этот раздел)
    Для каждой темы, идеи внутри блока — укажи примерный объём в знаках
    Делай пометки, где должны быть списки, таблицы, цитаты, визуальные элементы, FAQ и др.

    ЧЕКЛИСТ Проверь когда закончишь!
    Проверь указал ли явно заголовок H1 (нужна пометка H1)
    Структура блоков в чётко структурированном виде, с описанием, метками, объёмами.
    """
).strip()

ARTICLE_USER_PROMPT_TEMPLATE = textwrap.dedent(
    """
    <articleId>{article_id}</articleId>

    Напиши ПОЛНУЮ статью (≈15 000 зн.) по этому техническому заданию:
    ——————————————————————————————————————————
    {tz_text}
    ——————————————————————————————————————————
    """
).strip()

INSTRUCTIONS_ARTICLE = textwrap.dedent("""
    "Ты — технический копирайтер. Пиши статью строго по техническому заданию, только готовый HTML-текст. "
    "❗ НЕ оформляй ответ в виде markdown-блока ```html```. "
    "Без картинок и внешних ссылок. Используй только теги <h1>–<h6>, <p>, <ul>/<ol>, <table>. "
    "Стиль, структура, лексика — как в примерах из vector store. "
    "Не используй двоеточия и составные заголовки (только одна мысль на заголовок). "
    "Не используй формулы вроде «причины и что делать», «почему и как решить», «FAQ по теме» и т.п. "
    "В каждом заголовке — только отдельный смысл, вопрос или утверждение. "
    "Списки делай разнообразными: одни пункты могут быть длинными, другие — очень короткими, где-то только слово или пара слов, где-то развёрнутое объяснение. "
    "Не делай пункты одинаковыми по размеру или структуре. "
    "FAQ, таблицы и блоки вопросов — только если это реально уместно по содержанию, не добавляй шаблонных блоков автоматически. "
    "Старайся писать как реальный автор блога — с лёгкими отклонениями от шаблона, естественным языком, иногда разговорно, без излишней вылизанности. "
    "Запрещены обтекаемые формулировки («высока вероятность», «в некоторых случаях», «может быть»). "
    "Не вставляй универсальные пустые фразы («главный принцип — безопасность») без конкретики и действий. "
    "Избегай усреднения — глубина и объём разделов могут сильно различаться, не делай симметричных списков и блоков. "
    "Не вставляй текст ТЗ, не используй markdown, никаких служебных пометок — только содержимое статьи."
""").strip()

# Стоимость 
INPUT_COST_PER_M = 2.00
OUTPUT_COST_PER_M = 8.00

# Модель, лимиты, температура
MODEL_NAME = "gpt-4.1"
MAX_TOKENS_TZ = 3500
MAX_TOKENS_ARTICLE = 6000
TEMPERATURE = 1

# ─────────────────────────────── УТИЛИТЫ ───────────
def count_tokens(text: str, model=MODEL_NAME):
    try:
        encoder = tiktoken.encoding_for_model(model)
    except KeyError:
        encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))

def calculate_cost(tokens, input=True):
    tokens_in_millions = tokens / 1_000_000
    return tokens_in_millions * (INPUT_COST_PER_M if input else OUTPUT_COST_PER_M)

def load_openai_key() -> str:
    if (key := os.environ.get("OPENAI_API_KEY")):
        return key
    auth_file = BASE_DIR / "auth.json"
    if auth_file.exists():
        with auth_file.open(encoding="utf-8") as f:
            return json.load(f)["OPENAI_API_KEY"]
    raise RuntimeError("OpenAI key not found in env or auth.json")

def load_vector_store_id() -> str:
    state_file = BASE_DIR / "state.json"
    if not state_file.exists():
        raise RuntimeError("Файл state.json не найден в рабочей папке. В нём должен быть vector_store_id.")
    with state_file.open(encoding="utf-8") as f:
        state = json.load(f)
    if "vector_store_id" not in state:
        raise RuntimeError("В state.json отсутствует ключ vector_store_id.")
    return state["vector_store_id"]

def slugify(text_: str) -> str:
    text_ = re.sub(r"<[^>]+>", "", text_)
    text_ = re.sub(r"[^\w\s-]", "", text_, flags=re.U).strip().lower()
    return re.sub(r"[\s_-]+", "-", text_)

def parse_groups(csv_path: Path) -> list[str]:
    with csv_path.open(encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines[1:] 

def extract_keywords(block: str) -> List[Tuple[str, int]]:
    pairs: List[Tuple[str, int]] = []
    for part in re.split(r"[;\n]", block):
        if ":" not in part:
            continue
        key, freq = map(str.strip, part.split(":", 1))
        if freq.isdigit():
            pairs.append((key, int(freq)))
    return pairs

@retry(wait=wait_exponential_jitter(initial=1, max=20), stop=stop_after_attempt(3))
def chat_complete(client: OpenAI, messages: list[dict], max_tokens: int) -> tuple[str, int, int]:
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
    )
    content = response.choices[0].message.content.strip()
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else count_tokens(str(messages))
    completion_tokens = usage.completion_tokens if usage else count_tokens(content)
    return content, prompt_tokens, completion_tokens

# ─────────────────────────────── ОСНОВНАЯ ФУНКЦИЯ ───────────────────────
def generate_articles(input_csv: Path, groups_start: int, groups_end: Optional[int], save_html: bool = False):
    # Логи
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

    # Инициализация клиента и векторного хранилища
    client = OpenAI(api_key=load_openai_key())
    vector_store_id = load_vector_store_id()

    # Пути на хосте
    input_csv = input_csv if input_csv.is_absolute() else (BASE_DIR / input_csv)
    if not input_csv.exists():
        raise FileNotFoundError(f"Входной CSV не найден: {input_csv}")

    out_csv = BASE_DIR / "articles.csv"

    groups = parse_groups(input_csv)
    if groups_end is None:
        groups_slice = groups[groups_start:]
    else:
        groups_slice = groups[groups_start:groups_end]

    # CSV
    with out_csv.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["title", "slug", "tz", "html"])
        writer.writeheader()

        total_cost = 0.0
        saved_html_files = []

        pbar = tqdm(groups_slice, desc="Обработка групп", unit="grp", dynamic_ncols=True, disable=True)
        pbar.set_postfix_str(f"сумма ${total_cost:.4f}")

        for i, block in enumerate(pbar, 1):
            tqdm.write(f"Обрабатывается группа {i} из {len(groups_slice)}")

            keywords = extract_keywords(block)
            if not keywords:
                tqdm.write(f"⚠️ Группа {i} не содержит ключей — пропущена")
                pbar.update(0)
                continue

            main_query = keywords[0][0]
            phrases_block = "\n".join(f"{k} частотность {f}" for k, f in keywords)

            # ТЗ
            tz_prompt = TZ_USER_PROMPT_TEMPLATE.format(
                main_query=main_query, phrases_block=phrases_block
            )
            tz_messages = [
                {"role": "system", "content": SYSTEM_PROMPT_TZ},
                {"role": "user", "content": tz_prompt},
            ]
            tz_text, tz_in_tokens, tz_out_tokens = chat_complete(
                client, tz_messages, max_tokens=MAX_TOKENS_TZ
            )

            # Статья
            article_id = f"ID{i:05d}"
            art_prompt = ARTICLE_USER_PROMPT_TEMPLATE.format(
                article_id=article_id, tz_text=tz_text
            )

            response = client.responses.create(
                model="gpt-4.1",
                input=art_prompt,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                    "max_num_results": 10
                }],
                temperature=TEMPERATURE,
                max_output_tokens=10000,
                instructions=INSTRUCTIONS_ARTICLE
            )
            html_text = response.output_text.strip()

            # снять возможные ```html
            fence = re.compile(r"^```\\s*html\\s*|\\s*```$", re.I)
            html_text = "\n".join(
                line for line in html_text.splitlines() if not fence.match(line)
            ).strip()

            # Метаданные/заголовок
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, flags=re.I | re.S)
            title = html.unescape(h1.group(1).strip()) if h1 else main_query.title()
            slug = slugify(title)

            # Запись в общий CSV
            writer.writerow({"title": title, "slug": slug, "tz": tz_text, "html": html_text})
            tqdm.write(f"✅ Сохранено в CSV: {slug}")

            # (Опционально) сохранить отдельный html на хосте
            if save_html:
                out_dir = BASE_DIR / "output"
                out_dir.mkdir(exist_ok=True)
                out_file = out_dir / f"{slug}.html"
                out_file.write_text(html_text, encoding="utf-8")
                saved_html_files.append(str(out_file))
                tqdm.write(f"💾 HTML-файл сохранён на хосте: {out_file}")

            # Стоимость
            art_in_tokens = count_tokens(art_prompt)
            art_out_tokens = count_tokens(html_text)

            tz_cost = calculate_cost(tz_in_tokens, True) + calculate_cost(tz_out_tokens, False)
            art_cost = calculate_cost(art_in_tokens, True) + calculate_cost(art_out_tokens, False)
            art_total_cost = tz_cost + art_cost
            total_cost += art_total_cost

            tqdm.write(
                f"🔸 Токены ТЗ (in/out): {tz_in_tokens}/{tz_out_tokens} | "
                f"Статья (in/out): {art_in_tokens}/{art_out_tokens} | "
                f"Стоимость: ${art_total_cost:.4f} (сумма: ${total_cost:.4f})"
            )
            pbar.set_postfix_str(f"сумма ${total_cost:.4f}")

        pbar.close()

    tqdm.write(f"Готово → файл {out_csv}")
    tqdm.write(f"ИТОГОВАЯ сумма: ${total_cost:.4f}")

    return {
        "articles_csv": str(out_csv),
        "total_cost": round(total_cost, 4),
        "groups_processed": len(groups_slice),
        "saved_html_files": saved_html_files,
    }

# ─────────────────────────────── FASTAPI ────────────────────────────────
class GenerateRequest(BaseModel):
    input_csv: str
    groups_start: int = 0
    groups_end: Optional[int] = None  # null => до конца
    save_html: bool = False

app = FastAPI(title="Articles Generator API")

@app.post("/articles_generator")
def articles_generator(req: GenerateRequest):
    try:
        result = generate_articles(
            input_csv=Path(req.input_csv),
            groups_start=req.groups_start,
            groups_end=req.groups_end,
            save_html=req.save_html,
        )
        return {"ok": True, **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Лог и проброс
        logging.exception("Ошибка генерации")
        raise HTTPException(status_code=500, detail=str(e))


#if __name__ == "__main__":
    # Запускаем сервер на 0.0.0.0:8001
    #uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=False)


@app.post("/articles_generator_upload")
async def articles_generator_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    groups_start: int = Form(0),
    groups_end: int | None = Form(None),
    save_html: bool = Form(False),
    keep_server_copy: bool = Form(True),
):
    logging.info(f"UPLOAD start: {file.filename}, groups_start={groups_start}, groups_end={groups_end}, save_html={save_html}, keep={keep_server_copy}")
    # Сохраняем входной CSV именно в BASE_DIR (volume)
    tmp_name = f"{uuid.uuid4()}_{file.filename}"
    tmp_path = BASE_DIR / tmp_name
    with tmp_path.open("wb") as f:
        f.write(await file.read())

    try:
        result = generate_articles(
            input_csv=tmp_path,
            groups_start=groups_start,
            groups_end=groups_end,
            save_html=save_html,
        )
        csv_path = Path(result["articles_csv"])

        headers = {
        "X-Groups-Processed": str(result.get("groups_processed", "")),
        "X-Total-Cost": str(result.get("total_cost", "")),
        "X-Articles-Filename": csv_path.name,  # опционально
         }

        # Если не хотим хранить итоговый файл на сервере — удаляем после отдачи
        if not keep_server_copy:
            background.add_task(os.remove, csv_path)

        # В любом случае удаляем загруженный CSV после обработки
        background.add_task(os.remove, tmp_path)

        return FileResponse(
        csv_path,
        media_type="text/csv",
        filename="articles.csv",
        headers=headers,
        background=background,
        )
    except Exception:
        # На всякий случай подчистим загруженный CSV и при ошибке
        try: os.remove(tmp_path)
        except: pass
        raise


@app.post("/articles_generator_stream")
def articles_generator_stream(req: GenerateRequest):
    q = Queue()
    DONE = object()
    orig = tqdm.write

    def capture(msg, *a, **kw):
        text = msg if isinstance(msg, str) else str(msg)
        q.put(text)                 # отправляем строку прогресса
        orig(text, *a, **kw)        # и дублируем в серверные логи

    def worker():
        try:
            tqdm.write = capture
            result = generate_articles(
                input_csv=Path(req.input_csv),
                groups_start=req.groups_start,
                groups_end=req.groups_end,
                save_html=req.save_html,
            )
            q.put(json.dumps({"_result": result}, ensure_ascii=False))
        except Exception as e:
            q.put(json.dumps({"_error": str(e)}, ensure_ascii=False))
        finally:
            tqdm.write = orig
            q.put(DONE)

    Thread(target=worker, daemon=True).start()

    def gen():
        yield "event: start\ndata: processing started\n\n"
        while True:
            item = q.get()
            if item is DONE:
                break
            yield f"data: {item}\n\n"
        yield "event: end\ndata: done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")