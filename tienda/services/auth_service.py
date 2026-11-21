"""Inicio de sesión y control de acceso por rol (SIS-REC-01, SIS-REC-02)."""
import hashlib
import hmac
import os

ITERACIONES = 200_000
ROLES = ("administrador", "encargado_inventario", "cajero")


class AuthError(Exception):
    """Código o contraseña incorrectos, o usuario inactivo."""


class PermisoError(Exception):
    """El rol del usuario no tiene permiso para la operación."""


def hash_password(password, salt=None):
    """Devuelve (hash_hex, salt_hex). Nunca se guarda la contraseña en texto plano."""
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERACIONES)
    return h.hex(), salt.hex()


def crear_usuario(conn, codigo, nombre, password, rol):
    """Registra un usuario nuevo. Lo invoca el administrador."""
    if rol not in ROLES:
        raise ValueError(f"Rol inválido: {rol}")
    if len(password) < 3:
        raise ValueError("La contraseña debe tener al menos 3 caracteres.")
    h, s = hash_password(password)
    conn.execute(
        "INSERT INTO usuarios (codigo, nombre, password_hash, salt, rol) VALUES (?,?,?,?,?)",
        (codigo, nombre, h, s, rol),
    )


def iniciar_sesion(conn, codigo, password):
    """
    Valida código y contraseña.
    Retorna: dict {id, codigo, nombre, rol}.
    Errores: AuthError si las credenciales son incorrectas o el usuario está inactivo.
    """
    fila = conn.execute(
        "SELECT * FROM usuarios WHERE codigo = ? AND activo = 1", (codigo,)
    ).fetchone()
    if fila is None:
        raise AuthError("Código o contraseña incorrectos.")
    esperado = fila["password_hash"]
    obtenido, _ = hash_password(password, bytes.fromhex(fila["salt"]))
    if not hmac.compare_digest(esperado, obtenido):
        raise AuthError("Código o contraseña incorrectos.")
    return {"id": fila["id"], "codigo": fila["codigo"],
            "nombre": fila["nombre"], "rol": fila["rol"]}


def requiere_rol(usuario, *roles_permitidos):
    """Lanza PermisoError si el rol del usuario no está en la lista."""
    if usuario["rol"] not in roles_permitidos:
        raise PermisoError(
            f"El rol '{usuario['rol']}' no puede realizar esta operación."
        )
