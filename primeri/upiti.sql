-- Primeri upita za demonstraciju sistema (svaki red je jedan upit).
-- Pokrenuti: python pokreni_primere.py
-- ------------------------------------------------------------------
-- 1) Selekcija po sortirajućem (clustered) B+ ključu - jednakost
SELECT ime FROM Student WHERE indeks = '2020/0001'
-- 2) Selekcija - opseg po clustered B+ ključu + ORDER BY (već sortirano)
SELECT indeks, ime FROM Student WHERE indeks >= '2020/0100' ORDER BY indeks
-- 3) Složeni uslov (konjunkcija) nad jednom tabelom + ORDER BY (sort potreban)
SELECT s.indeks, s.ime FROM Student s WHERE s.smer = 'RN' AND s.prosek >= 8.5 ORDER BY s.prosek
-- 4) Hash indeks - jednakost na nesortirajućem hash indeksu
SELECT i.ispitId FROM Ispit i WHERE i.predmetId = 12 AND i.ocena = 10
-- 5) Spajanje dve tabele (sistem bira algoritam spajanja)
SELECT s.ime, i.ocena FROM Student s, Ispit i WHERE s.indeks = i.studentIndeks
-- 6) Spajanje sa selekcijom pre spajanja
SELECT s.ime, i.ocena FROM Student s, Ispit i WHERE s.indeks = i.studentIndeks AND i.ocena = 10
-- 7) Tri tabele + filter + ORDER BY (bira se i redosled spajanja)
SELECT s.ime, p.naziv, i.ocena FROM Student s, Ispit i, Predmet p WHERE s.indeks = i.studentIndeks AND i.predmetId = p.predmetId AND p.katedra = 'RTI' ORDER BY s.ime
-- 8) Četiri tabele
SELECT s.ime, p.naziv, st.tip FROM Student s, Ispit i, Predmet p, Stipendija st WHERE s.indeks = i.studentIndeks AND i.predmetId = p.predmetId AND s.indeks = st.studentIndeks AND st.tip = 'REDOVNA'
