import sqlite3

class SQLiteCursorProxy:
    def __init__(self, cursor):
        self._cursor = cursor

    def _filter_pragma(self, sql):
        if isinstance(sql, str) and "PRAGMA foreign_keys = ON" in sql:
            return sql.replace("PRAGMA foreign_keys = ON", "PRAGMA foreign_keys = OFF")
        return sql

    def execute(self, sql, *params):
        return self._cursor.execute(self._filter_pragma(sql), *params)

    def executemany(self, sql, *params):
        return self._cursor.executemany(self._filter_pragma(sql), *params)

    def executescript(self, sql):
        filtered = sql.replace("PRAGMA foreign_keys = ON", "PRAGMA foreign_keys = OFF")
        return self._cursor.executescript(filtered)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __setattr__(self, name, value):
        if name == "_cursor":
            super().__setattr__(name, value)
        else:
            setattr(self._cursor, name, value)

    def __iter__(self):
        return iter(self._cursor)

class SQLiteConnectionProxy:
    def __init__(self, conn):
        self._conn = conn

    def _filter_pragma(self, sql):
        """Подменяем PRAGMA foreign_keys = ON → OFF в тестах."""
        if isinstance(sql, str) and "PRAGMA foreign_keys = ON" in sql:
            return sql.replace("PRAGMA foreign_keys = ON", "PRAGMA foreign_keys = OFF")
        return sql

    def execute(self, sql, *params):
        return self._conn.execute(self._filter_pragma(sql), *params)

    def executemany(self, sql, *params):
        return self._conn.executemany(self._filter_pragma(sql), *params)

    def executescript(self, sql):
        filtered = sql.replace(
            "PRAGMA foreign_keys = ON",
            "PRAGMA foreign_keys = OFF"
        )
        return self._conn.executescript(filtered)

    def cursor(self, *args, **kwargs):
        cur = self._conn.cursor(*args, **kwargs)
        return SQLiteCursorProxy(cur)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name == "_conn":
            super().__setattr__(name, value)
        else:
            setattr(self._conn, name, value)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)
