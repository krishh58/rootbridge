import os, psycopg2

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM persons")
before = cur.fetchone()[0]
print(f'Before: {before:,} persons')

cur.execute("""
    DELETE FROM persons
    WHERE id NOT IN (
        SELECT MIN(id)
        FROM persons
        WHERE tree_id = (SELECT id FROM trees WHERE name = 'RootCommons Vault')
        GROUP BY first_name, last_name, birth_year, birth_state, birth_country
    )
    AND tree_id = (SELECT id FROM trees WHERE name = 'RootCommons Vault')
""")
conn.commit()

cur.execute("SELECT COUNT(*) FROM persons")
after = cur.fetchone()[0]
print(f'After: {after:,} persons')
print(f'Removed: {before - after:,} duplicates')

print('Running VACUUM ANALYZE to reclaim space...')
conn.autocommit = True
cur.execute('VACUUM ANALYZE persons')
print('Done.')
conn.close()
