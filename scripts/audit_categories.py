import sqlite3

conn = sqlite3.connect('data/renovai.db')
cur = conn.cursor()

print('=== ISSUE 1: Insulation categories ===')
print()
print('--- Line items with insulation keywords ---')
cur.execute("""
SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf, q.file_name
FROM line_items li JOIN quotes q ON li.quote_id = q.id
WHERE li.name_hu LIKE '%szigetel%' OR li.name_hu LIKE '%Szigetel%'
   OR li.name_hu LIKE '%hőszigetel%' OR li.name_hu LIKE '%Hőszigetel%'
   OR li.name_hu LIKE '%hangszigetel%' OR li.name_hu LIKE '%Hangszigetel%'
   OR li.name_hu LIKE '%vízszigetel%' OR li.name_hu LIKE '%Vízszigetel%'
   OR li.name_hu LIKE '%vizszigetel%' OR li.name_hu LIKE '%Vizszigetel%'
ORDER BY li.name_hu
""")
rows = cur.fetchall()
print(f'  Found {len(rows)} items')
for row in rows:
    labor = row[2] if row[2] else 0
    mat = row[3] if row[3] else 0
    total = row[4] if row[4] else 0
    print(f'  {row[0]:50s} cat={str(row[1]):20s} labor={labor:>10,d} mat={mat:>10,d} total={total:>10,d} quote={row[5]}')

print()
print('--- Line items by category_key = szigetelés ---')
cur.execute("SELECT category_key, COUNT(*), SUM(total_cost_huf) FROM line_items WHERE category_key = 'szigetelés' GROUP BY category_key")
rows = cur.fetchall()
for row in rows:
    print(f'  {row[0]:30s} count={row[1]:>3d}  total_cost={row[2]}')
if not rows:
    print('  (none found)')

print()
print('--- ALL category_key distribution ---')
cur.execute("SELECT category_key, COUNT(*) as cnt FROM line_items GROUP BY category_key ORDER BY cnt DESC")
for row in cur.fetchall():
    print(f'  {str(row[0]):30s} count={row[1]:>3d}')

print()
print('=== ISSUE 2: Nyilászáró csere ===')
print()
cur.execute("""
SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf, q.area_sqm, q.file_name
FROM line_items li JOIN quotes q ON li.quote_id = q.id
WHERE li.category_key = 'nyílászáró'
ORDER BY li.name_hu
""")
rows = cur.fetchall()
print(f'  Found {len(rows)} items with category_key=nyílászáró')
for row in rows:
    labor = row[2] if row[2] else 0
    mat = row[3] if row[3] else 0
    total = row[4] if row[4] else 0
    area = row[5] if row[5] else 55
    per_sqm_labor = labor / area if area and labor else 0
    print(f'  {row[0]:50s} labor={labor:>10,d} mat={mat:>10,d} total={total:>10,d} area={area:>6.1f} labor/nm={per_sqm_labor:>10,.0f} quote={row[6]}')

print()
print('--- Broader search: ablak/ajtó/nyílászáró in name_hu ---')
cur.execute("""
SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf, q.area_sqm, q.file_name
FROM line_items li JOIN quotes q ON li.quote_id = q.id
WHERE li.name_hu LIKE '%nyílászáró%' OR li.name_hu LIKE '%Nyílászáró%'
   OR li.name_hu LIKE '%ablak%' OR li.name_hu LIKE '%Ablak%'
   OR li.name_hu LIKE '%ajtó%' OR li.name_hu LIKE '%Ajtó%'
   OR li.name_hu LIKE '%nyilaszaro%'
ORDER BY li.name_hu
""")
rows = cur.fetchall()
print(f'  Found {len(rows)} items')
for row in rows:
    labor = row[2] if row[2] else 0
    mat = row[3] if row[3] else 0
    total = row[4] if row[4] else 0
    area = row[5] if row[5] else 55
    per_sqm_labor = labor / area if area and labor else 0
    print(f'  {row[0]:50s} cat={str(row[1]):20s} labor={labor:>10,d} mat={mat:>10,d} total={total:>10,d} area={area:>6.1f} labor/nm={per_sqm_labor:>10,.0f} quote={row[6]}')

print()
print('=== ISSUE 3: Laminált padló ===')
print()
cur.execute("""
SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf, q.file_name
FROM line_items li JOIN quotes q ON li.quote_id = q.id
WHERE li.name_hu LIKE '%laminált%' OR li.name_hu LIKE '%Laminált%'
   OR li.name_hu LIKE '%laminate%' OR li.name_hu LIKE '%Laminate%'
   OR li.name_hu LIKE '%vinyl%' OR li.name_hu LIKE '%Vinyl%'
ORDER BY li.name_hu
""")
rows = cur.fetchall()
print(f'  Found {len(rows)} laminate/vinyl items')
for row in rows:
    print(f'  {row[0]:50s} cat={str(row[1]):20s} labor={str(row[2]):>10s} mat={str(row[3]):>10s} total={str(row[4]):>10s} quote={row[5]}')

print()
print('--- parketta category items ---')
cur.execute("SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf FROM line_items li WHERE li.category_key = 'parketta' ORDER BY li.name_hu")
rows = cur.fetchall()
print(f'  Found {len(rows)} parketta items')
for row in rows:
    print(f'  {row[0]:50s} cat={str(row[1]):20s} labor={str(row[2]):>10s} mat={str(row[3]):>10s} total={str(row[4]):>10s}')

print()
print('--- burkolás category items ---')
cur.execute("SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf FROM line_items li WHERE li.category_key = 'burkolás' ORDER BY li.name_hu")
rows = cur.fetchall()
print(f'  Found {len(rows)} burkolás items')
for row in rows:
    print(f'  {row[0]:50s} cat={str(row[1]):20s} labor={str(row[2]):>10s} mat={str(row[3]):>10s} total={str(row[4]):>10s}')

print()
print('=== ISSUE 4: Klíma ===')
print()
cur.execute("""
SELECT li.name_hu, li.category_key, li.labor_cost_huf, li.material_cost_huf, li.total_cost_huf, q.file_name
FROM line_items li JOIN quotes q ON li.quote_id = q.id
WHERE li.category_key = 'klíma' OR li.name_hu LIKE '%klíma%' OR li.name_hu LIKE '%Klíma%'
ORDER BY li.name_hu
""")
rows = cur.fetchall()
print(f'  Found {len(rows)} klíma items')
for row in rows:
    print(f'  {row[0]:50s} cat={str(row[1]):20s} labor={str(row[2]):>10s} mat={str(row[3]):>10s} total={str(row[4]):>10s} quote={row[5]}')

conn.close()
