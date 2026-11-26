# -*- coding: utf-8 -*-
"""Проверка авто-бэкапа data/ (zip, хранение 7)."""

import json
import os
import sys
import tempfile
import zipfile

TMP = tempfile.mkdtemp(prefix="backup_check_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backup as bk

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


bk.DATA_DIR = os.path.join(TMP, "data")
bk.BACKUP_DIR = os.path.join(TMP, "backups")
bk.KEEP = 7
os.makedirs(bk.DATA_DIR)

with open(os.path.join(bk.DATA_DIR, "economy.json"), "w", encoding="utf-8") as f:
    json.dump({"a": 1}, f)
with open(os.path.join(bk.DATA_DIR, "tickets.json"), "w", encoding="utf-8") as f:
    json.dump({"b": 2}, f, ensure_ascii=False)
with open(os.path.join(bk.DATA_DIR, "broken.tmp"), "w", encoding="utf-8") as f:
    f.write("junk")

path = bk.make_backup()
check("zip created", path is not None and os.path.exists(path), str(path))
check("zip name", os.path.basename(path).startswith("data-") and path.endswith(".zip"))

with zipfile.ZipFile(path) as zf:
    names = sorted(zf.namelist())
    body = zf.read("data/economy.json").decode("utf-8")
check("only data json", names == ["data/economy.json", "data/tickets.json"], str(names))
check("content preserved", json.loads(body) == {"a": 1}, body)
check("tmp excluded", "data/broken.tmp" not in names, str(names))

# Хранение: старые архивы удаляются, свежие остаются
for i in range(10):
    open(os.path.join(bk.BACKUP_DIR, f"data-2020010{i}-000000.zip"), "wb").close()
bk._prune()
left = bk.list_backups()
check("keeps 7", len(left) == 7, str(len(left)))
check("drops oldest", "data-20200100-000000.zip" not in left, str(left[-1]))
check("keeps newest", "data-20200109-000000.zip" in left)
check("real backup kept", os.path.basename(path) in left)

# Нет папки data — бэкап не делается и не падает
bk.DATA_DIR = os.path.join(TMP, "nope")
check("no data dir -> None", bk.make_backup() is None)

# Пустая data/ — тоже None
bk.DATA_DIR = os.path.join(TMP, "empty")
os.makedirs(bk.DATA_DIR)
check("empty data -> None", bk.make_backup() is None)

# env-настройки читаются безопасно
check("keep floor", bk.KEEP >= 1)
check("interval sane", bk.INTERVAL_HOURS >= 1.0)

print(f"\nВсего провалов: {len(fails)}")
if fails:
    print("Провалились: " + ", ".join(fails))
sys.exit(1 if fails else 0)
