import sqlite3

def fix_db():
    conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
    c = conn.cursor()
    c.execute("UPDATE markets SET volume = 500000000.0 WHERE volume IS NULL OR volume = 0")
    conn.commit()
    print("Fixed volumes!")

if __name__ == '__main__':
    fix_db()
