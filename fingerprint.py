#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fingerprint.py — определение реальной базовой модели за OpenAI-совместимым
endpoint'ом по отпечатку токенизатора.

Метод: id токенов у каждой модели семейства свои. Скрипт прогоняет батарею
пробных строк через API (через prompt_token_ids), качает токенизаторы
моделей-кандидатов с HuggingFace и сравнивает id-последовательности.
Совпавший кандидат = базовое семейство. Слова модели при этом не спрашиваются
вообще — врать нечему.

Использование:
  python3 fingerprint.py --base https://api.example.com/v1 --key sk-... \
      --candidates Qwen/Qwen3.5-9B NousResearch/Meta-Llama-3-8B zai-org/GLM-4.5 \
      [--cache /tmp/tk_cache]

Требует: pip install tokenizers; доступ к huggingface.co.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

PROBES = [
    "Hello, world!",
    "The quick brown fox jumps over the lazy dog.",
    "café naïve Москва 北京 🌸",
    "def fib(n):\n    return n if n < 2 else fib(n-1) + fib(n-2)\n",
    "A" * 64,
    "había una vez un programador que usaba Ñ y ü",
    "zzzqqqxjvkwpq",
    "∫₀^∞ e^{-x²} dx = √π/2",
    "Токенизатор — это важно, товарищ.",
    "lorem ipsum dolor sit amet consectetur adipiscing elit",
    "{{ template }} <% erb %> <?php ?> [#markdown]",
    "assistant system user user_id temperature top_p",
    "1 22 333 4444 55555 666666",
    "uncensored fine-tune checkpoint manifest",
]


def http_json(url, headers, payload=None, timeout=180):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Endpoint:
    def __init__(self, base, key, model):
        self.base = base.rstrip("/")
        self.model = model
        self.headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "curl/8.10.1",
            "Accept": "*/*",
        }

    def prompt_ids(self, content):
        d = http_json(self.base + "/chat/completions", self.headers, {
            "model": self.model, "max_tokens": 1,
            "messages": [{"role": "user", "content": content}],
            "prompt_token_ids": True, "return_token_ids": True,
        })
        ids = d.get("prompt_token_ids")
        if not ids:
            raise RuntimeError("endpoint не отдаёт prompt_token_ids — отпечаток невозможен")
        return ids

    def content_ids(self, content):
        """Вырезает id самого user-сообщения из общего промпта (эвристика по маркерам)."""
        ids = self.prompt_ids(content)
        from collections import Counter
        freq = Counter(ids)
        markers = sorted([t for t, c in freq.items() if t >= 100000 and c >= 2],
                         key=lambda t: -freq[t])
        if not markers:
            raise RuntimeError("не нашёл старт-маркер шаблона в token ids")
        start = markers[0]
        # конец сообщения: следующий по частоте высокий id после старт-маркера
        end = next(t for t in markers if t != start)
        positions = [i for i in range(len(ids)) if ids[i] == start]
        # последний старт-маркер — прим-затравка ассистента, предпоследний — наше сообщение
        pos = positions[-2] if len(positions) >= 2 else positions[-1]
        stop = next((j for j in range(pos + 1, len(ids)) if ids[j] == end), len(ids))
        seg = ids[pos + 1:stop]
        # [старт, РОЛЬ, \n, ...контент...] — отсекаем токен роли и перевод строки
        if seg:
            seg = seg[1:]
        if seg and seg[0] == 198:
            seg = seg[1:]
        return seg


def fetch_tokenizer(repo, cache):
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, repo.replace("/", "_") + ".json")
    if not os.path.exists(path) or os.path.getsize(path) < 100_000:
        url = f"https://huggingface.co/{repo}/resolve/main/tokenizer.json"
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                blob = r.read()
        except urllib.error.HTTPError as e:
            print(f"  {repo}: скачать не удалось (HTTP {e.code})")
            return None
        if len(blob) < 100_000:
            print(f"  {repo}: файл подозрительно мал ({len(blob)}B) — пропуск")
            return None
        open(path, "wb").write(blob)
    return path


def main():
    ap = argparse.ArgumentParser(description="Tokenizer-отпечаток базовой модели")
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--candidates", nargs="+", required=True,
                    help="HF-репозитории моделей-кандидатов, например Qwen/Qwen3.5-9B")
    ap.add_argument("--cache", default="/tmp/tk_cache")
    ap.add_argument("--dump", default=None, help="куда сохранить собранные id (json)")
    args = ap.parse_args()

    from tokenizers import Tokenizer

    ep = Endpoint(args.base, args.key, args.model)
    print("== сбор id с endpoint'а ==")
    observed = {}
    for p in PROBES:
        try:
            observed[p] = ep.content_ids(p)
        except Exception as e:
            print(f"  {p[:30]!r}: {e}")
    if not observed:
        sys.exit("не удалось собрать ни одной пробы")
    if args.dump:
        json.dump(observed, open(args.dump, "w"), ensure_ascii=False, indent=1)

    print("\n== кандидаты ==")
    scores = []
    for repo in args.candidates:
        path = fetch_tokenizer(repo, args.cache)
        if not path:
            continue
        try:
            tok = Tokenizer.from_file(path)
        except Exception as e:
            print(f"  {repo}: tokenizer.json не читается ({e})")
            continue
        ok = 0
        for p, ref in observed.items():
            # многие шлюзы срезают хвостовые \n и пробелы в content — нормализуем
            enc = tok.encode(p.rstrip(), add_special_tokens=False).ids
            if enc == ref:
                ok += 1
        scores.append((ok, len(observed), repo))

    print("\n== результат ==")
    for ok, total, repo in sorted(scores, reverse=True):
        bar = "█" * round(20 * ok / total)
        verdict = "СОВПАДЕНИЕ" if ok == total else ("почти" if ok >= total * 0.8 else "")
        print(f"  {ok:>2}/{total}  {bar:<20} {repo}  {verdict}")
    print("\nПолное совпадение по всей батарее = это базовое семейство модели.")
    print("Частичное (80%+) — родственный токенизатор, проверьте special-токены вручную.")


if __name__ == "__main__":
    main()