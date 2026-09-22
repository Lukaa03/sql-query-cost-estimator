"""
Katalog šeme baze podataka.

Učitava ulazne podatke (veličina bafera + šema) iz JSON formata i gradi
objektni model nad kojim radi procenitelj cene. Statistike koje se čuvaju za
svaku tabelu i atribut su one koje su navedene u tekstu projekta:

  - za tabelu:   broj redova (n), broj blokova (b), broj redova po bloku (f)
  - za atribut:  tip, da li je jedinstven, broj različitih vrednosti V(A)
  - za indeks:   atribut(i), tip (B+ stablo / hash), da li je sortirajući
                 (clustered), visina stabla (za B+)

Pretpostavke (videti README): vrednosti min/max po atributima nisu date, pa se
za procenu selektivnosti poređenja koristi standardna pretpostavka iz literature
(Silberschatz) o jednoj polovini relacije.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Tipovi indeksa
# ---------------------------------------------------------------------------
BPLUS = "B_PLUS_TREE"
HASH = "HASH"


@dataclass
class Attribute:
    name: str
    type: str
    unique: bool
    distinct_values: int          # V(A, r) – broj različitih vrednosti


@dataclass
class Index:
    name: str
    attributes: list[str]         # atribut(i) nad kojima je indeks
    type: str                     # BPLUS ili HASH
    clustered: bool               # da li je sortirajući (primarni) indeks
    tree_height: int = 0          # visina B+ stabla (h_i); za hash se ne koristi

    @property
    def is_bplus(self) -> bool:
        return self.type == BPLUS

    @property
    def is_hash(self) -> bool:
        return self.type == HASH


@dataclass
class Table:
    name: str
    row_count: int                # n_r
    block_count: int              # b_r
    rows_per_block: int           # f_r (blocking factor)
    attributes: list[Attribute] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)

    # --- pomoćni pristup ----------------------------------------------------
    def attr(self, name: str) -> Optional[Attribute]:
        for a in self.attributes:
            if a.name == name:
                return a
        return None

    def distinct(self, attr_name: str) -> int:
        a = self.attr(attr_name)
        return a.distinct_values if a else self.row_count

    def is_unique(self, attr_name: str) -> bool:
        a = self.attr(attr_name)
        return bool(a and a.unique)

    def indexes_on(self, attr_name: str) -> list[Index]:
        """Svi indeksi čiji je PRVI atribut zadati atribut (iskoristiv za uslov)."""
        return [i for i in self.indexes if i.attributes and i.attributes[0] == attr_name]

    def __repr__(self) -> str:  # pragma: no cover - samo za debug
        return f"Table({self.name}, n={self.row_count}, b={self.block_count}, f={self.rows_per_block})"


@dataclass
class Catalog:
    buffer_blocks: int                          # M – veličina bafera u blokovima
    tables: dict[str, Table] = field(default_factory=dict)

    def table(self, name: str) -> Table:
        t = self.tables.get(name)
        if t is None:
            raise KeyError(f"Tabela '{name}' ne postoji u šemi.")
        return t


# ---------------------------------------------------------------------------
# Učitavanje iz JSON-a
# ---------------------------------------------------------------------------
def load_catalog(data: dict) -> Catalog:
    """Gradi katalog iz parsiranog JSON objekta."""
    buffer_blocks = int(data.get("bufferBlocks", data.get("buffer", 0)))
    if buffer_blocks < 3:
        raise ValueError("Bafer mora imati bar 3 bloka za smislenu evaluaciju.")

    schema = data.get("schema", data)
    tables: dict[str, Table] = {}

    for t in schema.get("tables", []):
        attrs = [
            Attribute(
                name=a["name"],
                type=a.get("type", "STRING"),
                unique=bool(a.get("unique", False)),
                distinct_values=int(a.get("distinctValues", a.get("distinct", 1))),
            )
            for a in t.get("attributes", [])
        ]
        idxs = [
            Index(
                name=i["name"],
                attributes=list(i["attributes"]),
                type=i.get("type", BPLUS),
                clustered=bool(i.get("clustered", False)),
                tree_height=int(i.get("treeHeight", 0)),
            )
            for i in t.get("indexes", [])
        ]
        table = Table(
            name=t["name"],
            row_count=int(t["rowCount"]),
            block_count=int(t["blockCount"]),
            rows_per_block=int(t.get("rowsPerBlock", max(1, round(t["rowCount"] / max(1, t["blockCount"]))))),
            attributes=attrs,
            indexes=idxs,
        )
        tables[table.name] = table

    return Catalog(buffer_blocks=buffer_blocks, tables=tables)


def load_catalog_file(path: str) -> tuple[Catalog, Optional[str]]:
    """Učitava katalog iz JSON fajla. Vraća (katalog, upit_ako_postoji)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    query = data.get("query")
    return load_catalog(data), query
