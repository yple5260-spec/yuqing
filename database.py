"""Shared SQL boundary for local SQLite and the platform PostgreSQL database."""
from contextlib import contextmanager
import os
import sqlite3
from pathlib import Path
import psycopg
from dotenv import load_dotenv

load_dotenv(os.environ.get('XIN_ENV_FILE', Path(__file__).with_name('.env')), override=False)
IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class Postgres:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        return self.connection.execute(sql.replace('?', '%s'), parameters)

    def executescript(self, script):
        for sql in script.split(';'):
            if not sql.strip() or sql.strip().startswith('PRAGMA'):
                continue
            for table in ('history', 'reviews', 'audit'):
                sql = sql.replace(f'{table}(id INTEGER PRIMARY KEY', f'{table}(id BIGSERIAL PRIMARY KEY')
            self.execute(sql.replace(' REAL', ' DOUBLE PRECISION'))

    def commit(self):
        self.connection.commit()


def is_postgres():
    return bool(os.environ.get('TYAPP_DATABASE_URL'))


@contextmanager
def connect(data):
    if is_postgres():
        with psycopg.connect(os.environ['TYAPP_DATABASE_URL'], connect_timeout=10, row_factory=row_factory) as db:
            yield Postgres(db)
    else:
        db = sqlite3.connect(Path(data) / 'xin.db', timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()
