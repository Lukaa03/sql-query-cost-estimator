"""
Jednostavan parser za podskup SQL-a koji projekat zahteva.

Podržano (prema tekstu zadatka):
  SELECT  lista atributa (samo atributi, bez operacija i agregatnih funkcija)
  FROM    do 4 tabele razdvojene zarezima (bez JOIN operatora), opcioni alias
  WHERE   konjunkcija (AND) od najviše 6 uslova
  ORDER BY  najviše jedan atribut (ASC/DESC)

Bez GROUP BY, bez podupita, bez disjunkcije.

Uslov u WHERE klauzuli je:
  - selekcioni:  kolona  OP  literal        (= < <= > >= <>)
  - spajajući:   kolona  =   kolona         (poređenje dva atributa iz raznih tabela)

Sintaksna/semantička provera se NE radi (precrtano u zadatku) – pretpostavlja se
da je upit ispravno zadat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


EQ, NE, LT, LE, GT, GE = "=", "<>", "<", "<=", ">", ">="
COMPARISONS = {LT, LE, GT, GE}


@dataclass
class ColRef:
    """Referenca na kolonu, npr. s.prosek ili prosek (alias opcioni)."""
    qualifier: Optional[str]   # alias ili ime tabele (može biti None pre rezolucije)
    name: str

    def __str__(self) -> str:
        return f"{self.qualifier}.{self.name}" if self.qualifier else self.name


@dataclass
class Condition:
    left: ColRef
    op: str
    right: object              # ColRef (spajanje) ili literal vrednost (selekcija)

    @property
    def is_join(self) -> bool:
        return isinstance(self.right, ColRef)

    @property
    def is_selection(self) -> bool:
        return not self.is_join

    def __str__(self) -> str:
        r = str(self.right) if isinstance(self.right, ColRef) else _fmt_lit(self.right)
        return f"{self.left} {self.op} {r}"


@dataclass
class TableRef:
    name: str
    alias: Optional[str] = None

    @property
    def key(self) -> str:
        return self.alias or self.name


@dataclass
class Query:
    select: list[ColRef]
    from_tables: list[TableRef]
    where: list[Condition]            # konjunkcija uslova
    order_by: Optional[ColRef] = None
    order_desc: bool = False


# ---------------------------------------------------------------------------
def _fmt_lit(v) -> str:
    return f"'{v}'" if isinstance(v, str) else str(v)


def _parse_literal(token: str):
    token = token.strip()
    if (token.startswith("'") and token.endswith("'")) or (
        token.startswith('"') and token.endswith('"')
    ):
        return token[1:-1]
    try:
        if re.fullmatch(r"[+-]?\d+", token):
            return int(token)
        return float(token)
    except ValueError:
        # neoznačeni string (npr. datum bez navodnika) – tretiramo kao string
        return token


def _parse_colref(token: str) -> ColRef:
    token = token.strip()
    if "." in token:
        q, n = token.split(".", 1)
        return ColRef(q.strip(), n.strip())
    return ColRef(None, token)


_OP_RE = re.compile(r"(<=|>=|<>|!=|=|<|>)")


def _parse_condition(text: str) -> Condition:
    m = _OP_RE.search(text)
    if not m:
        raise ValueError(f"Neispravan uslov: '{text}'")
    op = m.group(1)
    if op == "!=":
        op = NE
    left = text[: m.start()].strip()
    right = text[m.end():].strip()

    left_col = _parse_colref(left)
    # desna strana: ako liči na kolonu (ima tačku ili nije literal/broj/string)
    if (right.startswith("'") or right.startswith('"')
            or re.fullmatch(r"[+-]?\d+(\.\d+)?", right)):
        right_val = _parse_literal(right)
    elif "." in right:
        right_val = _parse_colref(right)
    else:
        # goli identifikator -> kolona (npr. join bez aliasa); ako je očito
        # nepoznat string literal bez navodnika, parser ga svejedno tretira kao
        # kolonu samo ako postoji takav atribut – ovo se rešava u rezoluciji.
        right_val = _parse_colref(right)
    return Condition(left_col, op, right_val)


def parse(sql: str) -> Query:
    """Parsira SQL string u Query objekat."""
    s = " ".join(sql.strip().rstrip(";").split())
    flags = re.IGNORECASE

    sel_m = re.search(r"\bSELECT\b(.*?)\bFROM\b", s, flags)
    from_m = re.search(r"\bFROM\b(.*?)(\bWHERE\b|\bORDER\s+BY\b|$)", s, flags)
    where_m = re.search(r"\bWHERE\b(.*?)(\bORDER\s+BY\b|$)", s, flags)
    order_m = re.search(r"\bORDER\s+BY\b(.*?)$", s, flags)

    if not sel_m or not from_m:
        raise ValueError("Upit mora imati SELECT i FROM klauzulu.")

    # SELECT
    select = [_parse_colref(t) for t in sel_m.group(1).split(",") if t.strip()]

    # FROM
    from_tables: list[TableRef] = []
    for part in from_m.group(1).split(","):
        toks = part.strip().split()
        if not toks:
            continue
        if len(toks) >= 2:
            alias = toks[-1]
            if alias.upper() == "AS" and len(toks) >= 3:
                alias = toks[-1]
            from_tables.append(TableRef(toks[0], toks[-1] if toks[-1].upper() != "AS" else None))
        else:
            from_tables.append(TableRef(toks[0]))

    # WHERE
    where: list[Condition] = []
    if where_m and where_m.group(1).strip():
        conds = re.split(r"\bAND\b", where_m.group(1), flags=flags)
        where = [_parse_condition(c) for c in conds if c.strip()]

    # ORDER BY
    order_by = None
    order_desc = False
    if order_m and order_m.group(1).strip():
        ob = order_m.group(1).strip().split()
        order_by = _parse_colref(ob[0])
        order_desc = len(ob) > 1 and ob[1].upper() == "DESC"

    return Query(select, from_tables, where, order_by, order_desc)
