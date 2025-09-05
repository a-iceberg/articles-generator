from __future__ import annotations

import logging, sys
log = logging.getLogger("uvicorn.error")  
import csv
import html
import json
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
from fastapi.responses import StreamingResponse, FileResponse
from threading import Thread
from queue import Queue
import uuid
from fastapi import Query

# логи
import logging

def _enable_timestamps_in_uvicorn_logs():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    for name in ("uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        if lg.handlers:
            for h in lg.handlers:
                h.setFormatter(fmt)
        else:
            h = logging.StreamHandler()
            h.setFormatter(fmt)
            lg.addHandler(h)
        lg.propagate = False

# ─────────────────────────────── НАСТРОЙКИ ПУТЕЙ ───────────────────────────────
# ВСЕ файлы читаем/пишем в хостовую папку (монтируемую как /work).
BASE_DIR = Path(os.getenv("HOST_WORKDIR", "/work"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────── ПРОМПТЫ ───────────────────
SYSTEM_PROMPT_TZ = (
    "Ты — автор экспертного блога о ремонте техники, совмещающий опыт мастера и журналиста. "
    "Пишешь для мастеров и обычных людей, уважительно, по делу, с опорой на официальные данные. "
    "Тон дружелюбный, но профессиональный, допускается лёгкий жаргон и бытовые примеры. "

    "ВНИМАНИЕ: запрещены двоеточия, тире, скобки, союзы «и/или» в заголовках любого уровня. "
    "Один заголовок — одна мысль или один вопрос. Заголовки длиной 2–5 слов, без кликбейта. "

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

SYSTEM_PROMPT_ARTICLE = (
    "Ты — технический копирайтер. Пиши статью строго по техническому заданию, только готовый HTML-текст. "
    "❗ НЕ оформляй ответ в виде markdown-блока ```html```. "
    "Без картинок и внешних ссылок. Используй только теги <h1>–<h6>, <p>, <ul>/<ol>, <table>. "
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
)

ARTICLE_USER_PROMPT_TEMPLATE = textwrap.dedent(
    """
    <articleId>{article_id}</articleId>

    Напиши ПОЛНУЮ статью (≈15 000 зн.) по этому техническому заданию:
    ——————————————————————————————————————————
    {tz_text}
    ——————————————————————————————————————————
    """
).strip()

# Модель и параметры Claude
MODEL_NAME = "claude-sonnet-4-20250514"
MAX_TOKENS_TZ = 3500
MAX_TOKENS_ARTICLE = 10000
TEMPERATURE = 1.0

# ─────────────────────────────── УТИЛИТЫ ───────────
def load_anthropic_key() -> str:
    if (key := os.environ.get("ANTHROPIC_API_KEY")):
        return key
    auth_file = BASE_DIR / "auth.json"
    if auth_file.exists():
        with auth_file.open(encoding="utf-8") as f:
            data = json.load(f)
            if "ANTHROPIC_API_KEY" in data:
                return data["ANTHROPIC_API_KEY"]
    raise RuntimeError("ANTHROPIC_API_KEY не найден ни в окружении, ни в auth.json")

def anthropic_cost_usd(input_tokens: int, output_tokens: int) -> float:
    # Sonnet 4: $3 / $15 за 1M токенов (in/out)
    cin = 3.0 / 1_000_000
    cout = 15.0 / 1_000_000
    return input_tokens * cin + output_tokens * cout

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

# ─────────────────────────────── КЛИЕНТ CLAUDE ─────────────────────────
def get_anthropic_client() -> Anthropic:
    return Anthropic(api_key=load_anthropic_key())

@retry(wait=wait_exponential_jitter(initial=1, max=20), stop=stop_after_attempt(3))
def claude_complete(client: Anthropic, system_prompt: str, user_text: str,
                    max_tokens: int, temperature: float) -> tuple[str, int, int]:
    msg = client.messages.create(
        model=MODEL_NAME,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    
    parts: list[str] = []
    for b in msg.content:
        if getattr(b, "type", None) == "text":
            parts.append(getattr(b, "text", ""))
    text = "".join(parts).strip()
    usage = getattr(msg, "usage", None)
    in_toks = getattr(usage, "input_tokens", 0) if usage else 0
    out_toks = getattr(usage, "output_tokens", 0) if usage else 0
    return text, in_toks, out_toks

# ─────────────────────────────── ОСНОВНАЯ ФУНКЦИЯ ───────────────────────
def generate_articles(input_csv: Path, groups_start: int, groups_end: Optional[int], save_html: bool = False, client_emit=None):
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # --- ТРАНСЛЯЦИЯ ЛОГОВ К КЛИЕНТУ (если передали client_emit) ---
    # Сохраняем оригинальные методы
    _orig_info = log.info
    _orig_warning = log.warning
    _orig_error = log.error
    _orig_exception = log.exception

    def _emit(prefix, msg, args):
        if client_emit:
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                client_emit(f"{ts} {prefix}:     {msg % args}")
            except Exception:
                pass

    def _info(msg, *args, **kwargs):
        _orig_info(msg, *args, **kwargs)
        _emit("INFO", msg, args)

    def _warning(msg, *args, **kwargs):
        _orig_warning(msg, *args, **kwargs)
        _emit("WARNING", msg, args)

    def _error(msg, *args, **kwargs):
        _orig_error(msg, *args, **kwargs)
        _emit("ERROR", msg, args)

    def _exception(msg, *args, **kwargs):
        _orig_exception(msg, *args, **kwargs)
        _emit("ERROR", msg, args)

    # Подменяем методы на время выполнения
    log.info = _info
    log.warning = _warning
    log.error = _error
    log.exception = _exception

    try:

        client = get_anthropic_client()

        # Пути на хосте
        input_csv = input_csv if input_csv.is_absolute() else (BASE_DIR / input_csv)
        if not input_csv.exists():
            raise FileNotFoundError(f"Входной CSV не найден: {input_csv}")

        out_csv = BASE_DIR / "articles.csv"

        groups = parse_groups(input_csv)
        log.info("Загружено групп: %d", len(groups))
        groups_slice = groups[groups_start:] if groups_end is None else groups[groups_start:groups_end]
        log.info("Будет обработано групп: %d (с %d по %d)", len(groups_slice), groups_start + 1, (groups_end or len(groups)))
        log.info("🚀 Старт обработки...")

        with out_csv.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=["title", "slug", "tz", "html"])
            writer.writeheader()

            total_cost = 0.0
            saved_html_files: list[str] = []

            for i, block in enumerate(groups_slice, 1):
                log.info("Обрабатывается группа %d из %d", i, len(groups_slice))

                keywords = extract_keywords(block)
                if not keywords:
                    log.warning("Группа %d не содержит ключей — пропущена", i)
                    continue

                main_query = keywords[0][0]
                phrases_block = "\n".join(f"{k} частотность {f}" for k, f in keywords)

                # 1) ТЗ => Claude
                tz_prompt = TZ_USER_PROMPT_TEMPLATE.format(
                    main_query=main_query, phrases_block=phrases_block
                )
                tz_text, tz_in_tokens, tz_out_tokens = claude_complete(
                    client, SYSTEM_PROMPT_TZ, tz_prompt,
                    max_tokens=MAX_TOKENS_TZ, temperature=TEMPERATURE
                )

                # 2) Статья => Claude
                article_id = f"ID{i:05d}"
                art_prompt = ARTICLE_USER_PROMPT_TEMPLATE.format(
                    article_id=article_id, tz_text=tz_text
                )
                html_text, art_in_tokens, art_out_tokens = claude_complete(
                    client, SYSTEM_PROMPT_ARTICLE, art_prompt,
                    max_tokens=MAX_TOKENS_ARTICLE, temperature=TEMPERATURE
                )

                # снять возможные ```html
                fence = re.compile(r"^```\s*html\s*$|^```$", re.I)
                html_text = "\n".join(
                    line for line in html_text.splitlines() if not fence.match(line)
                ).strip()

                # Метаданные/заголовок
                h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, flags=re.I | re.S)
                title = html.unescape(h1.group(1).strip()) if h1 else main_query.title()
                slug = slugify(title)

                # Запись в общий CSV
                writer.writerow({"title": title, "slug": slug, "tz": tz_text, "html": html_text})
                log.info("✅ Сохранено в CSV: %s", slug)

                # (Опционально) сохранить отдельный html на хосте
                if save_html:
                    out_dir = BASE_DIR / "output"
                    out_dir.mkdir(exist_ok=True)
                    out_file = out_dir / f"{slug}.html"
                    out_file.write_text(html_text, encoding="utf-8")
                    saved_html_files.append(str(out_file))
                    log.info("💾 HTML-файл сохранён на хосте: %s", out_file)

                # Стоимость (Anthropic)
                tz_cost  = anthropic_cost_usd(tz_in_tokens, tz_out_tokens)
                art_cost = anthropic_cost_usd(art_in_tokens, art_out_tokens)
                art_total_cost = tz_cost + art_cost
                total_cost += art_total_cost

                log.info(
                    "🔸 Токены ТЗ (in/out): %s/%s | Статья (in/out): %s/%s | Стоимость: $%.4f (сумма: $%.4f)",
                    tz_in_tokens, tz_out_tokens, art_in_tokens, art_out_tokens, art_total_cost, total_cost
                )

        log.info("Готово → файл %s", out_csv)
        log.info("ИТОГОВАЯ сумма: $%.4f", total_cost)

        return {
            "articles_csv": str(out_csv),
            "total_cost": round(total_cost, 4),
            "groups_processed": len(groups_slice),
            "saved_html_files": saved_html_files,
        }
    finally:
        # Восстанавливаем оригинальные методы, чтобы не влиять на параллельные запросы
        log.info = _orig_info
        log.warning = _orig_warning
        log.error = _orig_error
        log.exception = _orig_exception

# ─────────────────────────────── FASTAPI ────────────────────────────────
class GenerateRequest(BaseModel):
    input_csv: str
    groups_start: int = 0
    groups_end: Optional[int] = None  # null => до конца
    save_html: bool = False

app = FastAPI(title="Articles Generator API (Claude)")

@app.on_event("startup")
async def _setup_logging_format():
    _enable_timestamps_in_uvicorn_logs()
    
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
    except Exception:
        log.exception("Ошибка генерации")
        raise HTTPException(status_code=500, detail="Internal error")

@app.post("/articles_generator_upload")
async def articles_generator_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    groups_start: int = Form(0),
    groups_end: int | None = Form(None),
    save_html: bool = Form(False),
    keep_server_copy: bool = Form(True),
):
    log.info(
        "UPLOAD start: %s, groups_start=%s, groups_end=%s, save_html=%s, keep=%s",
        file.filename, groups_start, groups_end, save_html, keep_server_copy
    )
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
            "X-Articles-Filename": csv_path.name,
        }

        if not keep_server_copy:
            background.add_task(os.remove, csv_path)
        background.add_task(os.remove, tmp_path)

        return FileResponse(
            csv_path,
            media_type="text/csv",
            filename="articles.csv",
            headers=headers,
            background=background,
        )
    except Exception:
        try: os.remove(tmp_path)
        except: pass
        raise

@app.post("/articles_generator_stream_upload")
async def articles_generator_stream_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    groups_start: int = Form(0),
    groups_end: int | None = Form(None),
    save_html: bool = Form(False),
    keep_server_copy: bool = Form(True),
):
    """
    Загружаем CSV и сразу стримим клиенту процесс обработки (логи + результат).
    """
    tmp_name = f"{uuid.uuid4()}_{file.filename}"
    tmp_path = BASE_DIR / tmp_name
    with tmp_path.open("wb") as f:
        f.write(await file.read())

    q = Queue()
    DONE = object()

    def emit(line: str):
        q.put(f"data: {line}\n\n")

    def worker():
        try:
            emit(f"INFO:     UPLOAD start: {file.filename}, groups_start={groups_start}, groups_end={groups_end}, save_html={save_html}, keep={keep_server_copy}")
            result = generate_articles(
                input_csv=tmp_path,
                groups_start=groups_start,
                groups_end=groups_end,
                save_html=save_html,
                client_emit=emit,         
            )
            emit(json.dumps({
            "_result": {
                **result,
                "download_url": f"/download_once?path={result['articles_csv']}"
            }
        }, ensure_ascii=False))
        except Exception as e:
            emit(json.dumps({"_error": str(e)}, ensure_ascii=False))
        finally:
            if not keep_server_copy:
                try: os.remove(result["articles_csv"])
                except: pass
            try: os.remove(tmp_path)
            except: pass
            q.put(DONE)

    Thread(target=worker, daemon=True).start()

    def gen():
        yield "event: start\ndata: processing started\n\n"
        while True:
            item = q.get()
            if item is DONE:
                break
            yield item
        yield "event: end\ndata: done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")



@app.get("/download")
def download(path: str = Query(..., description="Абсолютный путь к файлу в контейнере")):
    p = Path(path).resolve()
    base = BASE_DIR.resolve()
    if not (p.exists() and (p == base or base in p.parents)):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(p, media_type="text/csv", filename=p.name)

@app.get("/download_once")
def download_once(path: str = Query(..., description="Абсолютный путь к файлу в контейнере")):
    p = Path(path).resolve()
    base = BASE_DIR.resolve()
    if not (p.exists() and (p == base or base in p.parents)):
        raise HTTPException(status_code=404, detail="Файл не найден")

    # отдаём файл и планируем его удалить после отдачи
    background = BackgroundTasks()
    background.add_task(os.remove, p)

    return FileResponse(
        p,
        media_type="text/csv",
        filename=p.name,
        background=background
    )