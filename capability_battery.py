#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capability_battery.py — подлинность модели: настоящая сильная или
переименованная слабая.

Вендора и харнесс вскрывает probe.py; этот скрипт отвечает на следующий
вопрос бриджа: под брендом "vendor-super-model-9" стоит реальная модель
фронтьер-класса или мелкая, которой просто сменили имя в /models?

Метод: батарея задач с ЗАРАНЕЕ ИЗВЕСТНЫМ верным ответом. Мелкие модели
сыпятся на ядре (подсчёт букв, арифметика), средние — на дискриминирующем
(доказательство с контрпримером). Смотрим не "понравился ответ", а верно/неверно.

Использование:
  python3 capability_battery.py --base https://api.example.com/v1 --key sk-... \
      [--model NAME] [--speed]

Только stdlib. Гоняет при temperature=0. Печатает вердикт.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 180


def http(url, headers, payload=None, timeout=TIMEOUT):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {"raw": e.read().decode("utf-8", "replace")[:400]}
        return None, {"http": e.code, "body": body}
    except Exception as e:
        return None, {"error": repr(e)}


def norm(s):
    """Нормализация для сверки: нижний регистр, без запятых/пробелов/пунктуации."""
    return re.sub(r"[,\s]", "", (s or "").lower())


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

    def ask(self, prompt, max_tokens=1200):
        body = {"model": self.model, "temperature": 0,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        d, err = http(self.base + "/chat/completions", self.headers, body)
        if err:
            return None, err, None
        ch = (d.get("choices") or [{}])[0]
        return ch.get("message", {}).get("content"), None, d.get("usage", {})


# (id, уровень, prompt, [списки обязательных подстрок — хотя бы одна на группу])
# Каждый элемент = группа синонимов; ответ засчитан, если в норм. тексте
# встречается хотя бы один вариант из КАЖДОЙ группы.
CORE = [
    ("bat_ball", "core",
     "A bat and a ball cost $1.10 total. The bat costs $1.00 more than the ball. "
     "How much does the ball cost? Answer with the amount only.",
     [["0.05", "5cents", "$.05", "0.050"]]),

    ("machines", "core",
     "If 5 machines take 5 minutes to make 5 widgets, how long would 100 machines "
     "take to make 100 widgets? Answer with the duration only.",
     [["5minute", "5minutes", "5min", "fiveminute"]]),

    ("letters", "core",
     "Count the letter 'r' in the word 'strawberry'. Then count 'r' in "
     "'refrigerator'. Then count 'e' in the sentence 'She sees three green trees'. "
     "Give three numbers separated by spaces, nothing else.",
     [["3"], ["4"], ["9"]]),

    ("mult", "core",
     "Compute exactly 847 * 293. Output the number only.",
     [["248171"]]),

    ("zeros100", "core",
     "How many trailing zeros are there in 100! (100 factorial)? Output the "
     "number only.",
     [["24"]]),
]

DISCRIMINATING = [
    ("n4plus4", "discriminating",
     "Prove or disprove: for every positive integer n, n^4 + 4 is composite "
     "(never prime). Give a rigorous proof or counterexample.",
     # должно быть: опровергнуто (n=1 -> 5 простое) И факторизация
     [["false", "disprove", "disproven", "ложн", "n=1", "n = 1", "counterexample",
       "опроверг", "not composite", "is prime"],
      ["(n^2-2n+2)", "(n²-2n+2)", "n^2-2n+2", "(n-1)^2+1", "sophie", "факторизац",
       "factoriz", "n^4+4=(", "n^4 + 4 = ("]]),

    ("seating", "discriminating",
     "Five people A,B,C,D,E sit in a row. A is not at either end. B is immediately "
     "left of C. D is not adjacent to A. E is at one of the ends. How many valid "
     "arrangements exist? Answer with the count, then list them.",
     [["2"]]),

    ("needle", "discriminating",
     "Counts: 47 apples, 12 oranges, 93 bananas, 5 pears, 61 grapes, 28 mangoes, "
     "77 plums, 34 cherries, 19 apricots, 88 peaches. What is the sum of the counts "
     "of the fruits whose count is a prime number? Output the number only.",
     [["132"]]),

    ("palindrome", "discriminating",
     "Write a Python function returning the longest palindromic substring of s. "
     "Then state the output for 'babad' and for 'cbbd'.",
     [["bab"], ["bb"]]),
]


def check(text, groups):
    n = norm(text)
    return all(any(norm(alt) in n for alt in grp) for grp in groups)


def run_suite(ep, suite):
    """Возвращает (passed, errored, results). errored != 0 значит запросы не
    дошли до модели (503/429/таймаут) — это НЕ провал способностей."""
    passed = errored = 0
    results = []
    for tid, level, prompt, groups in suite:
        text, err, usage = ep.ask(prompt)
        if err:
            errored += 1
            results.append((tid, False, True,
                            "ОШИБКА: " + json.dumps(err, ensure_ascii=False)[:160]))
            continue
        ok = check(text, groups)
        passed += ok
        preview = (text or "").strip().replace("\n", " ")[:100]
        results.append((tid, ok, False, preview))
    return passed, errored, results


def speed_test(ep):
    t0 = time.time()
    text, err, usage = ep.ask(
        "Write a detailed 400-word technical explanation of the TLS 1.3 handshake.",
        max_tokens=600)
    el = time.time() - t0
    if err or not usage:
        return None
    ct = usage.get("completion_tokens", 0)
    return (ct, el, ct / el if el else 0)


def main():
    ap = argparse.ArgumentParser(description="Подлинность модели: настоящая или переименованная")
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--speed", action="store_true", help="замерить ток/с (лишний запрос)")
    args = ap.parse_args()

    ep = Endpoint(args.base, args.key, args.model)
    if not ep.model:
        d, err = http(ep.base + "/models", ep.headers)
        if err or not d.get("data"):
            print("нет модели и /models недоступен — укажите --model")
            sys.exit(1)
        ep.model = d["data"][0].get("id")
    print(f"endpoint: {ep.base}\nmodel:    {ep.model}\n")

    print("=" * 72)
    print("ЯДРО (мелкие модели сыпятся здесь)")
    print("=" * 72)
    core_pass, core_err, core_res = run_suite(ep, CORE)
    for tid, ok, err, prev in core_res:
        tag = "ERR " if err else ("PASS" if ok else "FAIL")
        print(f"  [{tag}] {tid:14} {prev}")

    print("\n" + "=" * 72)
    print("ДИСКРИМИНИРУЮЩЕЕ (средний класс vs фронтьер)")
    print("=" * 72)
    disc_pass, disc_err, disc_res = run_suite(ep, DISCRIMINATING)
    for tid, ok, err, prev in disc_res:
        tag = "ERR " if err else ("PASS" if ok else "FAIL")
        print(f"  [{tag}] {tid:14} {prev}")

    if args.speed:
        print("\n" + "=" * 72)
        print("СКОРОСТЬ")
        print("=" * 72)
        sp = speed_test(ep)
        if sp:
            ct, el, tps = sp
            verdict = ("крупная модель" if tps < 35 else
                       "средняя/быстрая" if tps < 80 else "мелкая MoE (класс 30B-A3B)")
            print(f"  {ct} токенов за {el:.1f}s => {tps:.1f} ток/с  ({verdict})")
        else:
            print("  замер не удался (модель недоступна?)")

    print("\n" + "=" * 72)
    print("ВЕРДИКТ")
    print("=" * 72)
    print(f"  ядро:            {core_pass}/{len(CORE)}"
          + (f"  ({core_err} недоступно)" if core_err else ""))
    print(f"  дискриминирущее: {disc_pass}/{len(DISCRIMINATING)}"
          + (f"  ({disc_err} недоступно)" if disc_err else ""))

    total_err = core_err + disc_err
    total = len(CORE) + len(DISCRIMINATING)
    if total_err == total:
        print("  => МОДЕЛЬ НЕДОСТУПНА: все запросы упали (503/429/таймаут).")
        print("     Это НЕ провал способностей — под этим именем просто нет живого")
        print("     апстрима. Проверьте другое имя модели или /models.")
    elif total_err > 0:
        print(f"  => ЧАСТИЧНЫЙ РЕЗУЛЬТАТ: {total_err} проб не дошло до модели.")
        print("     Вердикт ниже — только по ответившим пробам, перепроверьте.")
    elif core_pass <= len(CORE) // 2:
        print("  => ПОД БРЕНДОМ ПЕРЕИМЕНОВАННАЯ СЛАБАЯ МОДЕЛЬ: провалено ядро.")
        print("     Что бы ни говорили /models и самоотчёт — способности ниже заявленных.")
    elif disc_pass >= len(DISCRIMINATING) - 1:
        print("  => НАСТОЯЩАЯ СИЛЬНАЯ МОДЕЛЬ (фронтьер-класс): ядро и дискриминирующее пройдены.")
        print("     Точную версию установить может быть нельзя (ids вырезаны, промпт")
        print("     запрещает раскрытие) — но класс подтверждён объективно.")
    else:
        print("  => СРЕДНИЙ КЛАСС: ядро пройдено, дискриминирующее частично.")
        print("     Не мелочь, но и не топ. Сверьте с обещанным в /models.")

    print("\n  ВАЖНО: имя модели и самоотчёт — НЕ доказательство. Только эта батарея.")


if __name__ == "__main__":
    main()
