"""
Procena cene i izbor plana evaluacije.

Cena se izražava u BROJU BLOK TRANSFERA. Model cene prati standardnu literaturu
(Silberschatz, Korth, Sudarshan – "Database System Concepts", pogl. 15–16), što
je gradivo predmeta. Sve formule i pretpostavke su dokumentovane u README.

Glavne komponente:
  * RelStats   – statistike relacije (bazne ili međurezultata)
  * PlanNode   – čvor plana izvršavanja (operacija + algoritam + cena)
  * selekcija  – izbor najboljeg pristupnog puta (scan / B+ / hash / složeni uslov)
  * spajanje   – NL, blok-NL, indeks-NL, objedinjeno (merge) i hash spajanje
  * sortiranje – spoljno objedinjeno sortiranje (ORDER BY)
  * projekcija – na bazi protoka (bez cene, jer nema DISTINCT-a)
  * materijalizacija – upis međurezultata na disk

Konvencija: cena svakog operatora obuhvata ČITANJE njegovih ulaza sa diska i
UPIS njegovog rezultata (materijalizacija), osim sortiranja kod kog upis runova
već ulazi u formulu, i projekcije koja se izvršava u protoku.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Optional

from catalog import Catalog, Table
from sqlparser import Query, ColRef, Condition, EQ, NE, COMPARISONS

ceil = math.ceil


# ---------------------------------------------------------------------------
# Spoljno objedinjeno sortiranje
# ---------------------------------------------------------------------------
def external_sort_cost(b: int, M: int) -> int:
    """Broj blok transfera za spoljno sortiranje relacije od b blokova, bafer M.

        b <= M            -> sve staje u memoriju, jedan prolaz: b
        inače             -> b * (2 * ceil(log_{M-1}(ceil(b/M))) + 1)
    """
    if b <= 0:
        return 0
    if b <= M:
        return b
    runs = ceil(b / M)                       # broj početnih sortiranih runova
    merge_passes = ceil(math.log(runs) / math.log(M - 1)) if runs > 1 else 0
    return b * (2 * merge_passes + 1)


# ---------------------------------------------------------------------------
# Statistike relacije
# ---------------------------------------------------------------------------
@dataclass
class RelStats:
    label: str
    n: int                                   # broj redova
    b: int                                   # broj blokova
    f: float                                 # redova po bloku (blocking factor)
    distinct: dict = field(default_factory=dict)   # (key, attr) -> V(A)
    sorted_on: Optional[tuple] = None        # (key, attr) ako je rezultat sortiran
    tables: set = field(default_factory=set)

    def V(self, col: tuple) -> int:
        return self.distinct.get(col, self.n)


# ---------------------------------------------------------------------------
# Čvor plana izvršavanja
# ---------------------------------------------------------------------------
@dataclass
class PlanNode:
    op: str                                  # naziv operacije
    algorithm: str                           # izabrani algoritam / detalj
    cost: float                              # cena OVOG čvora (blok transferi)
    out_tuples: int = 0
    out_blocks: int = 0
    children: list = field(default_factory=list)

    def total_cost(self) -> float:
        return self.cost + sum(c.total_cost() for c in self.children)

    # lep ispis stabla plana
    def render(self, indent: int = 0) -> str:
        pad = "    " * indent
        line = (f"{pad}- {self.op}: {self.algorithm}\n"
                f"{pad}    cena čvora = {self._fmt(self.cost)} bt"
                f"   |  izlaz ≈ {self.out_tuples} redova / {self.out_blocks} blokova")
        out = [line]
        for c in self.children:
            out.append(c.render(indent + 1))
        return "\n".join(out)

    @staticmethod
    def _fmt(x: float) -> str:
        return str(int(x)) if abs(x - round(x)) < 1e-9 else f"{x:.1f}"


# ===========================================================================
#  Razrešavanje kolona (alias -> tabela)
# ===========================================================================
class Resolver:
    def __init__(self, catalog: Catalog, query: Query):
        self.catalog = catalog
        self.key_to_table: dict[str, Table] = {}
        self.name_to_key: dict[str, str] = {}
        for tr in query.from_tables:
            table = catalog.table(tr.name)
            key = tr.key
            self.key_to_table[key] = table
            self.name_to_key[key] = key
            self.name_to_key[tr.name] = key

    def resolve(self, col: ColRef) -> tuple[str, Table, str]:
        """Vraća (key, table, attr_name)."""
        if col.qualifier:
            key = self.name_to_key.get(col.qualifier)
            if key is None:
                raise KeyError(f"Nepoznata tabela/alias '{col.qualifier}'.")
            return key, self.key_to_table[key], col.name
        # nekvalifikovan atribut – nađi tabelu koja ga ima
        for key, table in self.key_to_table.items():
            if table.attr(col.name):
                return key, table, col.name
        raise KeyError(f"Atribut '{col.name}' ne postoji ni u jednoj tabeli iz FROM.")


# ===========================================================================
#  Procena selektivnosti
# ===========================================================================
def cond_matching_tuples(table: Table, attr: str, op: str) -> int:
    """Broj redova koje uslov (na jednom atributu) propušta."""
    n = table.row_count
    if op == EQ:
        if table.is_unique(attr):
            return 1
        return max(1, round(n / max(1, table.distinct(attr))))
    if op == NE:
        return n                              # ~ skoro sve
    # poređenje (< <= > >=): bez min/max statistike -> pretpostavka n/2
    return max(1, round(n / 2))


def selection_fraction(table: Table, conds: list[Condition], resolver: Resolver) -> float:
    """Kombinovana selektivnost (proizvod) konjunkcije selekcionih uslova."""
    frac = 1.0
    for c in conds:
        _, _, attr = resolver.resolve(c.left)
        frac *= cond_matching_tuples(table, attr, c.op) / table.row_count
    return frac


# ===========================================================================
#  SELEKCIJA – izbor najboljeg pristupnog puta
# ===========================================================================
@dataclass
class AccessPath:
    cost: float
    algorithm: str
    sorted_attr: Optional[str] = None


def _index_prefix_match(table: Table, index, sel_by_attr: dict[str, Condition]):
    """Koliko vodećih atributa indeksa pokriva konjunkcija (prefiks pravilo).

    Vraća (matched_attrs, mt_fraction, stops_with_range).
    Za B+ stablo opseg (poređenje) prekida prefiks; za hash su dozvoljene
    samo jednakosti i to nad SVIM atributima indeksa.
    """
    matched = []
    frac = 1.0
    stops_range = False
    for a in index.attributes:
        c = sel_by_attr.get(a)
        if c is None:
            break
        if c.op == EQ:
            matched.append(a)
            frac *= cond_matching_tuples(table, a, EQ) / table.row_count
        elif c.op in COMPARISONS and index.is_bplus:
            matched.append(a)
            frac *= cond_matching_tuples(table, a, c.op) / table.row_count
            stops_range = True
            break
        else:
            break
    return matched, frac, stops_range


def choose_access_path(table: Table, key: str, conds: list[Condition],
                       resolver: Resolver, M: int) -> AccessPath:
    n, b, f = table.row_count, table.block_count, table.rows_per_block

    # mapiranje atribut -> uslov (za prefiks poklapanje indeksa)
    sel_by_attr: dict[str, Condition] = {}
    has_eq_on_unique = False
    for c in conds:
        _, _, attr = resolver.resolve(c.left)
        sel_by_attr.setdefault(attr, c)
        if c.op == EQ and table.is_unique(attr):
            has_eq_on_unique = True

    candidates: list[AccessPath] = []

    # A1 – linearno pretraživanje (uvek moguće)
    candidates.append(AccessPath(b, "Linearno pretraživanje (scan A1)", None))
    # A2 – linearni scan sa ranim prekidom na jednakost po ključu (prosečno b/2)
    if has_eq_on_unique:
        candidates.append(AccessPath(max(1, ceil(b / 2)),
                                     "Linearno pretraživanje, rani prekid na ključ (A2)", None))

    # indeksni pristupni putevi
    for idx in table.indexes:
        matched, frac, stops_range = _index_prefix_match(table, idx, sel_by_attr)
        if not matched:
            continue
        mt = max(1, round(n * frac))
        lead = idx.attributes[0]
        attrs_txt = ",".join(idx.attributes[:len(matched)])

        if idx.is_bplus:
            h = idx.tree_height
            if idx.clustered:
                cost = h + ceil(mt / f)
                sorted_attr = lead
                kind = "sortirajući"
            else:
                # nesortirajući: po jedan pristup bloku za svaki pogodak
                only_eq_key = (len(matched) == 1 and not stops_range
                               and table.is_unique(lead))
                cost = h + (1 if only_eq_key else mt)
                sorted_attr = None
                kind = "nesortirajući"
            op_kind = "opseg" if stops_range else "jednakost"
            candidates.append(AccessPath(
                cost, f"B+ stablo '{idx.name}' ({kind}, {op_kind} na [{attrs_txt}])", sorted_attr))

        elif idx.is_hash:
            # hash podržava samo jednakost i to nad svim atributima indeksa
            if stops_range or len(matched) != len(idx.attributes):
                continue
            if idx.clustered:
                cost = 1 + ceil(mt / f)
            else:
                cost = 1 + (1 if table.is_unique(lead) and len(idx.attributes) == 1 else mt)
            candidates.append(AccessPath(
                cost, f"Hash indeks '{idx.name}' (jednakost na [{attrs_txt}])", None))

    return min(candidates, key=lambda p: p.cost)


def _base_sorted_on(table: Table, key: str) -> Optional[tuple]:
    """Ako tabela ima sortirajući (clustered) B+ indeks, izlaz je sortiran po njemu."""
    for idx in table.indexes:
        if idx.is_bplus and idx.clustered and idx.attributes:
            return (key, idx.attributes[0])
    return None


def build_selection(table: Table, key: str, conds: list[Condition],
                    resolver: Resolver, M: int) -> tuple[PlanNode, RelStats]:
    n, b, f = table.row_count, table.block_count, table.rows_per_block
    label_suffix = f" ({key})" if key != table.name else ""

    # --- bez selekcionih uslova: bazna tabela je list; njeno ČITANJE plaća
    #     roditeljski operator (spajanje / sortiranje / projekcija). Nema cene
    #     ovde i nema materijalizacije suvišne kopije.
    if not conds:
        distinct = {(key, a.name): a.distinct_values for a in table.attributes}
        node = PlanNode(
            op=f"Pristup tabeli {table.name}{label_suffix}",
            algorithm="bazna tabela (čita je nadređeni operator; bez filtera)",
            cost=0, out_tuples=n, out_blocks=b,
        )
        stats = RelStats(label=key, n=n, b=b, f=f, distinct=distinct,
                         sorted_on=_base_sorted_on(table, key), tables={key})
        return node, stats

    # --- sa uslovima: selekcija je operator (pristup + materijalizacija) ---
    path = choose_access_path(table, key, conds, resolver, M)
    frac = selection_fraction(table, conds, resolver)
    res_n = max(1, round(n * frac))
    res_b = max(1, ceil(res_n / f))
    materialize = res_b
    node_cost = path.cost + materialize

    distinct = {}
    eq_attrs = {resolver.resolve(c.left)[2] for c in conds if c.op == EQ}
    for a in table.attributes:
        col = (key, a.name)
        distinct[col] = 1 if a.name in eq_attrs else min(a.distinct_values, res_n)

    sorted_on = (key, path.sorted_attr) if path.sorted_attr else None
    node = PlanNode(
        op=f"Selekcija nad {table.name}{label_suffix}",
        algorithm=path.algorithm + f"; materijalizacija rezultata (+{materialize} bt upis)",
        cost=node_cost, out_tuples=res_n, out_blocks=res_b,
    )
    stats = RelStats(label=key, n=res_n, b=res_b, f=f, distinct=distinct,
                     sorted_on=sorted_on, tables={key})
    return node, stats


# ===========================================================================
#  SPAJANJE
# ===========================================================================
def _equality_index_probe_cost(table: Table, attr: str) -> Optional[float]:
    """Cena jedne pretrage indeksa po jednakosti na atributu (za indeks-NL)."""
    best = None
    for idx in table.indexes:
        if idx.attributes[0] != attr:
            continue
        mt = 1 if table.is_unique(attr) else max(1, round(table.row_count / max(1, table.distinct(attr))))
        if idx.is_bplus:
            h = idx.tree_height
            cost = h + (1 if (table.is_unique(attr) or idx.clustered) else mt)
            if idx.clustered:
                cost = h + ceil(mt / table.rows_per_block)
        else:  # hash
            cost = 1 + (1 if table.is_unique(attr) else mt)
        best = cost if best is None else min(best, cost)
    return best


def build_join(left: RelStats, left_node: PlanNode,
               right: RelStats, right_node: PlanNode,
               join_pred: Optional[tuple], catalog: Catalog,
               resolver: Resolver, M: int) -> tuple[PlanNode, RelStats]:
    """Spaja dve relacije. join_pred = ((keyL,attrL),(keyR,attrR)) ili None (Dekart)."""
    bL, nL, fL = left.b, left.n, left.f
    bR, nR, fR = right.b, right.n, right.f

    # --- procena veličine rezultata ---
    if join_pred:
        (colL, colR) = join_pred
        VL, VR = left.V(colL), right.V(colR)
        res_n = max(1, round(nL * nR / max(1, VL, VR)))
    else:
        res_n = nL * nR
    f_res = 1.0 / (1.0 / fL + 1.0 / fR)
    res_b = max(1, ceil(res_n / f_res))

    algos: list[tuple[float, str, Optional[tuple]]] = []

    # ---- 1) Ugnežđena petlja (tuple nested-loop) ----
    nlj_lo = bL + nL * bR              # L spolja
    nlj_ro = bR + nR * bL             # R spolja
    if nlj_lo <= nlj_ro:
        algos.append((nlj_lo, f"Ugnežđena petlja (spolja {left.label})", None))
    else:
        algos.append((nlj_ro, f"Ugnežđena petlja (spolja {right.label})", None))

    # ---- 2) Blok ugnežđena petlja ----
    def bnl(b_out, b_in):
        return b_out + ceil(b_out / max(1, (M - 2))) * b_in
    c1 = bnl(bL, bR)
    c2 = bnl(bR, bL)
    if c1 <= c2:
        algos.append((c1, f"Blok ugnežđena petlja (spolja {left.label}, bafer {M})", None))
    else:
        algos.append((c2, f"Blok ugnežđena petlja (spolja {right.label}, bafer {M})", None))

    # ---- 3) Indeks ugnežđena petlja (samo ako je unutrašnja BAZNA tabela sa indeksom) ----
    if join_pred:
        (colL, colR) = join_pred
        # desna kao bazna tabela sa indeksom na colR
        if len(right.tables) == 1 and right.label in resolver.key_to_table:
            tR = resolver.key_to_table[right.label]
            probe = _equality_index_probe_cost(tR, colR[1])
            if probe is not None:
                cost = bL + nL * probe
                algos.append((cost, f"Indeks ugnežđena petlja (spolja {left.label}, "
                                    f"indeks na {right.label}.{colR[1]}, pretraga≈{PlanNode._fmt(probe)})", None))
        # leva kao bazna tabela sa indeksom na colL
        if len(left.tables) == 1 and left.label in resolver.key_to_table:
            tL = resolver.key_to_table[left.label]
            probe = _equality_index_probe_cost(tL, colL[1])
            if probe is not None:
                cost = bR + nR * probe
                algos.append((cost, f"Indeks ugnežđena petlja (spolja {right.label}, "
                                    f"indeks na {left.label}.{colL[1]}, pretraga≈{PlanNode._fmt(probe)})", None))

    # ---- 4) Objedinjeno (merge) spajanje ----
    if join_pred:
        (colL, colR) = join_pred
        sortL = 0 if left.sorted_on == colL else external_sort_cost(bL, M)
        sortR = 0 if right.sorted_on == colR else external_sort_cost(bR, M)
        cost = sortL + sortR + bL + bR
        txt = "Objedinjeno (merge) spajanje"
        extra = []
        if sortL:
            extra.append(f"sort {left.label}=+{sortL}")
        if sortR:
            extra.append(f"sort {right.label}=+{sortR}")
        if extra:
            txt += " [" + ", ".join(extra) + "]"
        algos.append((cost, txt, colL))   # rezultat sortiran po atributu spajanja (leve strane)

    # ---- 5) Hash spajanje ----
    if join_pred:
        smaller = min(bL, bR)
        # da li manja relacija staje u (M-1) particija svaka veličine <= M
        if smaller <= (M - 1) * M:
            cost = 3 * (bL + bR)
            txt = "Hash spajanje"
        else:
            passes = ceil(math.log(smaller / M) / math.log(M - 1)) if smaller > M else 1
            cost = (2 * passes + 1) * (bL + bR)
            txt = f"Hash spajanje (rekurzivno particionisanje, {passes} prolaza)"
        algos.append((cost, txt, None))

    best_cost, best_algo, sorted_attr = min(algos, key=lambda x: x[0])
    node_cost = best_cost + res_b        # + materijalizacija rezultata

    # statistike izlaza
    distinct = dict(left.distinct)
    distinct.update(right.distinct)
    if join_pred:
        (colL, colR) = join_pred
        v = min(left.V(colL), right.V(colR))
        distinct[colL] = v
        distinct[colR] = v
    for k in list(distinct):
        distinct[k] = min(distinct[k], res_n)

    label = f"({left.label}⋈{right.label})"
    sorted_on = sorted_attr if sorted_attr else None
    pred_txt = (f"{join_pred[0][0]}.{join_pred[0][1]} = {join_pred[1][0]}.{join_pred[1][1]}"
                if join_pred else "DEKARTOV PROIZVOD (nema uslov spajanja)")

    node = PlanNode(
        op="Spajanje",
        algorithm=f"{best_algo}  |  uslov: {pred_txt}; materijalizacija (+{res_b} bt)",
        cost=node_cost,
        out_tuples=res_n,
        out_blocks=res_b,
        children=[left_node, right_node],
    )
    stats = RelStats(label=label, n=res_n, b=res_b, f=f_res, distinct=distinct,
                     sorted_on=sorted_on, tables=left.tables | right.tables)
    return node, stats


# ===========================================================================
#  OPTIMIZATOR – glavni ulaz
# ===========================================================================
def _classify_conditions(query: Query, resolver: Resolver):
    """Razdvaja WHERE uslove na selekcione (po tabeli) i spajajuće (parovi)."""
    selections: dict[str, list[Condition]] = {tr.key: [] for tr in query.from_tables}
    joins: list[tuple[tuple, tuple, Condition]] = []   # ((keyL,attrL),(keyR,attrR),cond)
    for c in query.where:
        if c.is_join:
            kl, _, al = resolver.resolve(c.left)
            kr, _, ar = resolver.resolve(c.right)
            if kl == kr:
                # uslov nad istom tabelom koji poredi dva atributa -> tretiraj kao selekciju
                selections[kl].append(c)
            else:
                joins.append(((kl, al), (kr, ar), c))
        else:
            kl, _, _ = resolver.resolve(c.left)
            selections[kl].append(c)
    return selections, joins


def _find_pred(joined_keys: set, new_key: str, joins: list):
    """Nađi uslov spajanja između već spojenog skupa i nove tabele."""
    for (lcol, rcol, cond) in joins:
        kl, kr = lcol[0], rcol[0]
        if kl in joined_keys and kr == new_key:
            return (lcol, rcol)
        if kr in joined_keys and kl == new_key:
            return (rcol, lcol)
    return None


def optimize(catalog: Catalog, query: Query) -> tuple[PlanNode, dict]:
    """Vraća (koren plana, info-rečnik). Bira najjeftiniji levo-duboki plan."""
    M = catalog.buffer_blocks
    resolver = Resolver(catalog, query)
    selections, joins = _classify_conditions(query, resolver)

    # 1) Selekcije nad svim baznim tabelama (gura se naniže) – računa se jednom
    base_nodes: dict[str, tuple[PlanNode, RelStats]] = {}
    for tr in query.from_tables:
        table = catalog.table(tr.name)
        node, stats = build_selection(table, tr.key, selections[tr.key], resolver, M)
        base_nodes[tr.key] = (node, stats)

    keys = [tr.key for tr in query.from_tables]
    selection_cost = sum(n.cost for n, _ in base_nodes.values())

    # 2) Redosled spajanja – probaj sve permutacije (≤ 4 tabele), levo-duboko
    best = None  # (total_join_cost, root_node, final_stats)
    if len(keys) == 1:
        node, stats = base_nodes[keys[0]]
        best = (0, node, stats)
    else:
        for perm in itertools.permutations(keys):
            cur_node, cur_stats = base_nodes[perm[0]]
            joined = {perm[0]}
            jcost = 0.0
            for k in perm[1:]:
                pred = _find_pred(joined, k, joins)
                rnode, rstats = base_nodes[k]
                jnode, jstats = build_join(cur_stats, cur_node, rstats, rnode,
                                           pred, catalog, resolver, M)
                jcost += jnode.cost
                cur_node, cur_stats = jnode, jstats
                joined.add(k)
            if best is None or jcost < best[0]:
                best = (jcost, cur_node, cur_stats)

    _, root, final_stats = best

    # 3) ORDER BY – spoljno sortiranje ako rezultat nije već sortiran po tom atributu
    order_cost = 0
    if query.order_by is not None:
        k, _, a = resolver.resolve(query.order_by)
        target = (k, a)
        if final_stats.sorted_on == target:
            sort_node = PlanNode(
                op="ORDER BY",
                algorithm=f"rezultat je već sortiran po {k}.{a} (bez dodatne cene)",
                cost=0, out_tuples=final_stats.n, out_blocks=final_stats.b,
                children=[root])
        else:
            order_cost = external_sort_cost(final_stats.b, M)
            sort_node = PlanNode(
                op="ORDER BY",
                algorithm=f"Spoljno objedinjeno sortiranje po {k}.{a} (bafer {M})",
                cost=order_cost, out_tuples=final_stats.n, out_blocks=final_stats.b,
                children=[root])
        root = sort_node

    # 4) Projekcija (SELECT lista atributa) – u protoku.
    #    Ako je upit nad jednom tabelom bez ijednog uslova i bez ORDER BY, niko
    #    još nije pročitao tabelu, pa projekcija izvodi scan (b blok transfera).
    sel_txt = ", ".join(str(c) for c in query.select)
    single_unread = (len(keys) == 1 and base_nodes[keys[0]][0].cost == 0
                     and query.order_by is None)
    if single_unread:
        scan_b = base_nodes[keys[0]][1].b
        proj = PlanNode(
            op="Projekcija (SELECT)",
            algorithm=f"[{sel_txt}] – scan tabele uz projekciju u protoku",
            cost=scan_b, out_tuples=final_stats.n, out_blocks=final_stats.b,
            children=[root])
    else:
        proj = PlanNode(
            op="Projekcija (SELECT)",
            algorithm=f"[{sel_txt}] – izvršava se u protoku, bez dodatne cene (nema DISTINCT)",
            cost=0, out_tuples=final_stats.n, out_blocks=final_stats.b,
            children=[root])
    root = proj

    info = {
        "buffer": M,
        "selection_cost": selection_cost,
        "join_cost": best[0],
        "order_cost": order_cost,
        "total": root.total_cost(),
    }
    return root, info
