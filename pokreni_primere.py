"""Pokreće sve primere upita iz primeri/upiti.sql nad šemom primeri/sema.json."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEMA = os.path.join(HERE, "primeri", "sema.json")
UPITI = os.path.join(HERE, "primeri", "upiti.sql")


def main() -> int:
    with open(UPITI, encoding="utf-8") as fh:
        queries = [ln.strip() for ln in fh
                   if ln.strip() and not ln.strip().startswith("--")]
    for i, q in enumerate(queries, 1):
        print(f"\n\n##################  PRIMER {i}  ##################")
        rc = subprocess.run(
            [sys.executable, os.path.join(HERE, "main.py"), SEMA, "-q", q]
        ).returncode
        if rc != 0:
            print(f"[primer {i} završen sa kodom {rc}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
