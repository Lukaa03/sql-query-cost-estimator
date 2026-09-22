"""
Sistem za procenu izvršavanja SQL upita – glavni program.

Pokretanje:
    python main.py <ulaz.json> [--query "SELECT ... "]
    python main.py <ulaz.json> --query-file upit.sql

Ulazni JSON sadrži:
    bufferBlocks : veličina bafera u broju blokova
    schema       : opis tabela, atributa i indeksa (vidi primer_ulaza.json)
    query        : (opciono) SQL upit kao string

Ako je upit zadat i u JSON-u i preko --query, prednost ima --query.
Izlaz: izabrani plan evaluacije sa algoritmom i cenom svake operacije,
te ukupna procenjena cena u broju blok transfera.
"""

from __future__ import annotations

import argparse
import sys

from catalog import load_catalog_file
from sqlparser import parse
from planner import optimize, PlanNode


def _fmt(x: float) -> str:
    return str(int(x)) if abs(x - round(x)) < 1e-9 else f"{x:.1f}"


def run(json_path: str, sql: str) -> int:
    catalog, json_query = load_catalog_file(json_path)
    sql = sql or json_query
    if not sql:
        print("GREŠKA: upit nije zadat (ni u JSON-u pod 'query', ni preko --query).",
              file=sys.stderr)
        return 2

    query = parse(sql)
    root, info = optimize(catalog, query)

    bar = "=" * 78
    print(bar)
    print("PROCENA IZVRŠAVANJA SQL UPITA")
    print(bar)
    print("Upit:")
    print("   " + " ".join(sql.split()))
    print(f"\nVeličina bafera: {info['buffer']} blokova")
    print(f"\nTabele u upitu: " + ", ".join(
        f"{tr.name}"
        f"({catalog.table(tr.name).row_count} r / {catalog.table(tr.name).block_count} bl)"
        for tr in query.from_tables))
    print(f"\n{bar}\nPLAN EVALUACIJE (odozdo nagore se izvršava)\n{bar}")
    print(root.render())
    print(bar)
    print("PROCENA CENE (blok transferi):")
    print(f"   selekcije / pristup tabelama : {_fmt(info['selection_cost'])}")
    print(f"   spajanja                     : {_fmt(info['join_cost'])}")
    print(f"   sortiranje (ORDER BY)        : {_fmt(info['order_cost'])}")
    print(f"   {'-'*40}")
    print(f"   UKUPNO                       : {_fmt(info['total'])} blok transfera")
    print(bar)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Procena cene izvršavanja SQL upita (BP2).")
    ap.add_argument("json", help="putanja do ulaznog JSON fajla (bafer + šema [+ query])")
    ap.add_argument("--query", "-q", help="SQL upit kao string")
    ap.add_argument("--query-file", "-f", help="putanja do fajla sa SQL upitom")
    args = ap.parse_args(argv)

    sql = args.query
    if not sql and args.query_file:
        with open(args.query_file, "r", encoding="utf-8") as fh:
            sql = fh.read()
    return run(args.json, sql)


if __name__ == "__main__":
    raise SystemExit(main())
