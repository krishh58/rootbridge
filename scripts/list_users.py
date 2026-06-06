import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT email FROM users')
for row in cur.fetchall():
    print(row[0])
conn.close()
