"""Provera: nezavisno preračunavanje cena i rubni slučajevi (sanity testovi)."""
import math
from catalog import load_catalog
from sqlparser import parse
from planner import optimize, external_sort_cost

ceil = math.ceil

SEMA = {
    "bufferBlocks": 10,
    "schema": {"tables": [
        {"name": "Student", "rowCount": 1000, "blockCount": 100, "rowsPerBlock": 10,
         "attributes": [
             {"name": "indeks", "type": "STRING", "unique": True, "distinctValues": 1000},
             {"name": "smer", "type": "STRING", "unique": False, "distinctValues": 5},
             {"name": "prosek", "type": "DOUBLE", "unique": False, "distinctValues": 300}],
         "indexes": [
             {"name": "ix_ind", "attributes": ["indeks"], "type": "B_PLUS_TREE",
              "clustered": True, "treeHeight": 3}]},
        {"name": "Ispit", "rowCount": 8000, "blockCount": 800, "rowsPerBlock": 10,
         "attributes": [
             {"name": "studentIndeks", "type": "STRING", "unique": False, "distinctValues": 1000},
             {"name": "ocena", "type": "INT", "unique": False, "distinctValues": 6}],
         "indexes": [
             {"name": "ix_st", "attributes": ["studentIndeks"], "type": "B_PLUS_TREE",
              "clustered": False, "treeHeight": 3}]},
    ]},
}

passed = 0
failed = 0


def check(name, got, exp):
    global passed, failed
    ok = abs(got - exp) < 1e-6
    print(f"[{'OK ' if ok else 'NE!'}] {name}: dobijeno={got}, očekivano={exp}")
    passed += ok
    failed += not ok


cat = load_catalog(SEMA)

# 1) spoljno sortiranje 100 blokova, M=10: runs=10, passes=ceil(log9(10))=2 -> 100*(2*2+1)=500
check("external_sort(100,10)", external_sort_cost(100, 10), 500)
check("external_sort(5,10) staje u memoriju", external_sort_cost(5, 10), 5)

# 2) jednakost po clustered ključu: h+1=4 (+materijalizacija 1) = 5
root, info = optimize(cat, parse("SELECT smer FROM Student WHERE indeks = 'x'"))
check("clustered B+ jednakost ukupno", info["total"], 5)

# 3) linearni scan po neindeksiranom atributu: scan b=100 + materijalizacija
#    smer='RN' -> 1000/5=200 redova -> 20 blokova; 100+20=120
root, info = optimize(cat, parse("SELECT indeks FROM Student WHERE smer = 'RN'"))
check("scan + materijalizacija", info["total"], 120)

# 4) ORDER BY po clustered ključu bez WHERE: scan 100 (čita ga sort) + sort 500?
#    nema WHERE -> bazni list; ORDER BY indeks je već sortiran (clustered) -> 0;
#    projekcija mora da skenira? ne, ima ORDER BY koji čita: ali je već sortiran,
#    pa sort=0 i niko ne čita. Proverićemo samo da ne puca i da je >=0.
root, info = optimize(cat, parse("SELECT indeks FROM Student ORDER BY indeks"))
check("ORDER BY već sortiran (sort=0)", info["order_cost"], 0)

# 5) jednotabela bez WHERE i bez ORDER BY: projekcija skenira tabelu = b = 100
root, info = optimize(cat, parse("SELECT indeks, smer FROM Student"))
check("scan jednotabela bez uslova", info["total"], 100)

# 6) hash join Student-Ispit: očekivano spajanje > 0 i ukupno > selekcije
root, info = optimize(cat, parse(
    "SELECT smer, ocena FROM Student s, Ispit i WHERE s.indeks = i.studentIndeks"))
check("spajanje ima cenu", info["join_cost"] > 0, True)

# 7) parser: ORDER BY DESC i nekvalifikovani atributi
q = parse("SELECT a, t.b FROM T1 t, T2 WHERE t.a = 5 AND b > 3 ORDER BY a DESC")
check("parser broj uslova", len(q.where), 2)
check("parser ORDER DESC", q.order_desc, True)
check("parser broj tabela", len(q.from_tables), 2)

print(f"\nREZULTAT: {passed} prošlo, {failed} palo")
raise SystemExit(1 if failed else 0)
