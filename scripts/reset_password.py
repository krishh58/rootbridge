import os, bcrypt, psycopg2

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
h = bcrypt.hashpw(b'RootBridge2026!', bcrypt.gensalt()).decode()
cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (h, 'krishndrsn@gmail.com'))
conn.commit()
print(f'Done. Rows updated: {cur.rowcount}')
conn.close()
