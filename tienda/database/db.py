"""Conexión, transacciones y esquema de la base de datos (SQLite)."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection(ruta="tienda.db"):
    """Abre la base de datos. Las transacciones se controlan manualmente."""
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None          # control manual de transacciones
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaccion(conn):
    """Todo lo que ocurra dentro se guarda completo o no se guarda nada."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('administrador','encargado_inventario','cajero')),
    activo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo_barras TEXT UNIQUE,
    nombre TEXT NOT NULL,
    categoria TEXT,
    precio REAL NOT NULL CHECK (precio >= 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    stock_minimo INTEGER NOT NULL DEFAULT 5,
    activo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS metodos_pago (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    es_efectivo INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ventas (
    folio INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_hora TEXT NOT NULL,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    metodo_pago_id INTEGER NOT NULL REFERENCES metodos_pago(id),
    total REAL NOT NULL,
    monto_recibido REAL NOT NULL,
    cambio REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS detalle_venta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folio INTEGER NOT NULL REFERENCES ventas(folio),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad INTEGER NOT NULL,
    precio_unitario REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('entrada','salida','ajuste')),
    cantidad_anterior INTEGER NOT NULL,
    cantidad INTEGER NOT NULL,
    cantidad_nueva INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    fecha_hora TEXT NOT NULL,
    referencia TEXT
);
CREATE TABLE IF NOT EXISTS pedidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_hora TEXT NOT NULL,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','recibido'))
);
CREATE TABLE IF NOT EXISTS detalle_pedido (
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad_pedida INTEGER NOT NULL,
    cantidad_recibida INTEGER,
    PRIMARY KEY (pedido_id, producto_id)
);
CREATE TABLE IF NOT EXISTS discrepancias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad_pedida INTEGER NOT NULL,
    cantidad_recibida INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    fecha_hora TEXT NOT NULL
);
"""


def init_db(conn):
    """Crea las tablas si no existen."""
    conn.executescript(ESQUEMA)
