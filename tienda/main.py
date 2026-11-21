"""
Sistema de Gestión de Tienda Internacional (Interfaz Tkinter).
Incluye: Dashboard, POS Multi-moneda con Ticket, Catálogo Completo,
Carga Masiva CSV, Pedidos/Recepción, Historial de Ventas, Gestión de Usuarios
y Actualización/Respaldo Automático cada 5 minutos.
"""
import csv
import os
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from database.db import get_connection, init_db
from services import auth_service as auth
from services.venta_service import buscar_producto, registrar_venta, VentaError
from services.productos_service import (
    listar_productos, crear_producto, modificar_producto,
    desactivar_producto, ProductoError
)
from services.inventario_service import cargar_inventario_masivo, InventarioError
from services.recepcion_service import (
    crear_pedido, confirmar_recepcion, RecepcionError, DiscrepanciaPendiente
)
from services.backup_manager import generar_respaldo, BackupError
from services.sync_daemon import sincronizar_precios, SyncError

RUTA_BD = "tienda.db"
INTERVALO_AUTO_MS = 5 * 60 * 1000  # 5 minutos (300,000 ms)

# Tasas de cambio base MXN para operación internacional
TASAS_CAMBIO = {
    "MXN ($)": 1.0,
    "USD ($)": 0.058,
    "EUR (€)": 0.053,
}


def inicializar_sistema(conn):
    """Inicializa tablas, métodos de pago y garantiza que los usuarios predefinidos existan."""
    init_db(conn)

    # Usuarios predefinidos del sistema
    usuarios_predefinidos = [
        ("omar", "Omar (Administrador)", "123", "administrador"),
        ("alejandro", "Alejandro (Cajero)", "1234", "cajero"),
        ("eddy", "Eddy (Encargado de Inventario)", "12345", "encargado_inventario"),
    ]
    for codigo, nombre, password, rol in usuarios_predefinidos:
        existe = conn.execute(
            "SELECT 1 FROM usuarios WHERE codigo = ?", (codigo,)
        ).fetchone()
        if not existe:
            auth.crear_usuario(conn, codigo, nombre, password, rol)

    # Métodos de pago internacionales
    conn.execute(
        "INSERT OR IGNORE INTO metodos_pago (nombre, es_efectivo) VALUES "
        "('Efectivo', 1), ('Tarjeta Crédito/Débito', 0), ('Transferencia / Apple Pay', 0)"
    )

    # Productos iniciales solo si el catálogo está completamente vacío
    if conn.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0:
        conn.execute(
            "INSERT OR IGNORE INTO productos "
            "(codigo_barras, nombre, categoria, precio, stock, stock_minimo) VALUES "
            "('750100', 'Coca-Cola 600ml', 'Bebidas', 18.0, 20, 5), "
            "('750200', 'Sabritas Original', 'Botanas', 17.5, 15, 5), "
            "('750300', 'Leche Entera 1L', 'Lácteos', 26.0, 10, 4)"
        )
    conn.commit()


class TiendaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sistema Tienda")
        self.geometry("1120x700")
        self.minsize(980, 620)

        self.style = ttk.Style(self)
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self.conn = get_connection(RUTA_BD)
        inicializar_sistema(self.conn)
        self.usuario = None
        self.timer_auto_id = None
        self.ultima_actualizacion = "Pendiente"

        self.mostrar_login()

    def limpiar_ventana(self):
        if self.timer_auto_id:
            self.after_cancel(self.timer_auto_id)
            self.timer_auto_id = None
        for widget in self.winfo_children():
            widget.destroy()

    # =========================================================================
    # PANTALLA DE INICIO DE SESIÓN (SIN PISTAS DE CONTRASEÑAS)
    # =========================================================================
    def mostrar_login(self):
        self.limpiar_ventana()
        self.usuario = None

        contenedor = ttk.Frame(self, padding=40)
        contenedor.place(relx=0.5, rely=0.5, anchor="center")

        ttk.Label(
            contenedor, text="GLOBAL RETAIL POS", font=("Segoe UI", 22, "bold")
        ).grid(row=0, column=0, columnspan=2, pady=(0, 5))
        ttk.Label(
            contenedor, text="Acceso Corporativo al Sistema", foreground="gray"
        ).grid(row=1, column=0, columnspan=2, pady=(0, 25))

        ttk.Label(contenedor, text="Usuario:").grid(row=2, column=0, sticky="e", padx=8, pady=8)
        self.entry_codigo = ttk.Entry(contenedor, width=28, font=("Segoe UI", 10))
        self.entry_codigo.grid(row=2, column=1, pady=8)
        self.entry_codigo.focus()

        ttk.Label(contenedor, text="Contraseña:").grid(row=3, column=0, sticky="e", padx=8, pady=8)
        self.entry_pass = ttk.Entry(contenedor, width=28, show="*", font=("Segoe UI", 10))
        self.entry_pass.grid(row=3, column=1, pady=8)

        ttk.Button(
            contenedor, text="Iniciar Sesión", command=self.hacer_login
        ).grid(row=4, column=0, columnspan=2, pady=20, sticky="ew", ipady=5)

        self.bind("<Return>", lambda e: self.hacer_login())

    def hacer_login(self):
        codigo = self.entry_codigo.get().strip().lower()
        password = self.entry_pass.get()
        try:
            self.usuario = auth.iniciar_sesion(self.conn, codigo, password)
            self.unbind("<Return>")
            self.mostrar_menu_principal()
        except auth.AuthError as e:
            messagebox.showerror("Acceso Denegado", str(e))

    # =========================================================================
    # INTERFAZ PRINCIPAL + BARRA DE ESTADO + TEMPORIZADOR 5 MINUTOS
    # =========================================================================
    def mostrar_menu_principal(self):
        self.limpiar_ventana()

        # Barra superior
        barra = ttk.Frame(self, padding=(15, 10))
        barra.pack(fill="x")
        ttk.Label(
            barra,
            text=f"Global Retail POS  |  {self.usuario['nombre']}  ({self.usuario['rol'].upper()})",
            font=("Segoe UI", 11, "bold")
        ).pack(side="left")

        ttk.Button(barra, text="Cerrar Sesión", command=self.mostrar_login).pack(side="right", padx=5)
        ttk.Button(barra, text="Actualizar Ahora", command=self.ejecutar_actualizacion_global).pack(side="right", padx=5)

        # Notebook principal
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        rol = self.usuario["rol"]

        # Pestañas según permisos
        self.crear_tab_dashboard()

        if rol in ("cajero", "administrador"):
            self.crear_tab_ventas()
        if rol in ("administrador", "encargado_inventario"):
            self.crear_tab_productos()
            self.crear_tab_carga_masiva()
        if rol in ("encargado_inventario", "administrador"):
            self.crear_tab_recepcion()
        if rol in ("administrador", "cajero"):
            self.crear_tab_historial_ventas()
        if rol == "administrador":
            self.crear_tab_admin()

        # Barra de estado inferior
        status_bar = ttk.Frame(self, relief="sunken", padding=(10, 4))
        status_bar.pack(fill="x", side="bottom")
        self.lbl_status = ttk.Label(
            status_bar,
            text="Auto-actualización activa (cada 5 min) | Última actualización: recién iniciado",
            font=("Segoe UI", 9)
        )
        self.lbl_status.pack(side="left")

        # Iniciar ciclo automático de 5 minutos
        self.programar_actualizacion_5min()

    def programar_actualizacion_5min(self):
        """Programa la actualización automática cada 5 minutos (300,000 ms)."""
        self.timer_auto_id = self.after(INTERVALO_AUTO_MS, self.ciclo_actualizacion_automatica)

    def ciclo_actualizacion_automatica(self):
        self.ejecutar_actualizacion_global(silencioso=True)
        self.programar_actualizacion_5min()

    def ejecutar_actualizacion_global(self, silencioso=False):
        """Refresca vistas, verifica alertas y genera respaldo automático."""
        ahora = datetime.now().strftime("%H:%M:%S")
        self.ultima_actualizacion = ahora

        if hasattr(self, "actualizar_dashboard"):
            self.actualizar_dashboard()
        if hasattr(self, "tree_prod"):
            self.cargar_tabla_productos()
        if hasattr(self, "tree_busq"):
            self.buscar_para_venta()
        if hasattr(self, "tree_ventas"):
            self.cargar_historial_ventas()

        # Respaldo automático silencioso si es administrador
        extra = ""
        if self.usuario and self.usuario["rol"] == "administrador":
            try:
                generar_respaldo(RUTA_BD, "backups")
                extra = " | Respaldo automático OK"
            except Exception:
                extra = " | Respaldo pendiente"

        if hasattr(self, "lbl_status"):
            self.lbl_status.config(
                text=f"Auto-actualización activa (cada 5 min) | Última actualización: {ahora}{extra}"
            )
        if not silencioso:
            messagebox.showinfo("Actualizado", f"Datos sincronizados correctamente a las {ahora}.")

    # =========================================================================
    # PESTAÑA 0: DASHBOARD EJECUTIVO
    # =========================================================================
    def crear_tab_dashboard(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Resumen Ejecutivo")

        kpi_frame = ttk.Frame(tab)
        kpi_frame.pack(fill="x", pady=10)

        self.kpi_ventas = self._crear_tarjeta_kpi(kpi_frame, "Ventas Registradas", "0", 0)
        self.kpi_ingresos = self._crear_tarjeta_kpi(kpi_frame, "Ingresos Totales (MXN)", "$0.00", 1)
        self.kpi_productos = self._crear_tarjeta_kpi(kpi_frame, "Productos Activos", "0", 2)
        self.kpi_alertas = self._crear_tarjeta_kpi(kpi_frame, "Alertas Stock Bajo", "0", 3)

        ttk.Label(
            tab, text="Productos con Stock Bajo (Requieren Reabastecimiento)",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", pady=(15, 5))

        cols = ("codigo", "nombre", "categoria", "stock", "minimo")
        self.tree_alertas = ttk.Treeview(tab, columns=cols, show="headings", height=12)
        for col, txt, w in [
            ("codigo", "Código", 120), ("nombre", "Producto", 280),
            ("categoria", "Categoría", 150), ("stock", "Stock Actual", 100), ("minimo", "Stock Mínimo", 100)
        ]:
            self.tree_alertas.heading(col, text=txt)
            self.tree_alertas.column(col, width=w)
        self.tree_alertas.pack(fill="both", expand=True)

        self.actualizar_dashboard()

    def _crear_tarjeta_kpi(self, parent, titulo, valor, col):
        card = ttk.LabelFrame(parent, text=titulo, padding=15)
        card.grid(row=0, column=col, padx=8, sticky="nsew")
        parent.columnconfigure(col, weight=1)
        lbl = ttk.Label(card, text=valor, font=("Segoe UI", 18, "bold"))
        lbl.pack()
        return lbl

    def actualizar_dashboard(self):
        total_ventas = self.conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        ingresos = self.conn.execute("SELECT COALESCE(SUM(total), 0) FROM ventas").fetchone()[0]
        prods_activos = self.conn.execute("SELECT COUNT(*) FROM productos WHERE activo = 1").fetchone()[0]
        bajos = self.conn.execute(
            "SELECT codigo_barras, nombre, categoria, stock, stock_minimo "
            "FROM productos WHERE activo = 1 AND stock <= stock_minimo ORDER BY stock ASC"
        ).fetchall()

        self.kpi_ventas.config(text=str(total_ventas))
        self.kpi_ingresos.config(text=f"${ingresos:,.2f}")
        self.kpi_productos.config(text=str(prods_activos))
        self.kpi_alertas.config(text=str(len(bajos)))

        for item in self.tree_alertas.get_children():
            self.tree_alertas.delete(item)
        for p in bajos:
            self.tree_alertas.insert("", "end", values=(
                p["codigo_barras"], p["nombre"], p["categoria"], p["stock"], p["stock_minimo"]
            ))

    # =========================================================================
    # PESTAÑA 1: PUNTO DE VENTA INTERNACIONAL (POS)
    # =========================================================================
    def crear_tab_ventas(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Punto de Venta (POS)")
        self.carrito = []

        # Barra de búsqueda y moneda
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Buscar producto (código o nombre):").pack(side="left")
        self.entry_buscar_venta = ttk.Entry(top, width=32)
        self.entry_buscar_venta.pack(side="left", padx=5)
        self.entry_buscar_venta.bind("<KeyRelease>", lambda e: self.buscar_para_venta())
        ttk.Button(top, text="Buscar", command=self.buscar_para_venta).pack(side="left", padx=5)

        ttk.Label(top, text="Divisa de cobro:").pack(side="right", padx=(10, 5))
        self.combo_divisa = ttk.Combobox(
            top, values=list(TASAS_CAMBIO.keys()), state="readonly", width=10
        )
        self.combo_divisa.current(0)
        self.combo_divisa.pack(side="right")
        self.combo_divisa.bind("<<ComboboxSelected>>", lambda e: self.actualizar_vista_carrito())

        # Catálogo disponible
        cols_busq = ("id", "codigo", "nombre", "categoria", "precio", "stock")
        self.tree_busq = ttk.Treeview(tab, columns=cols_busq, show="headings", height=7)
        for col, txt, w in [
            ("id", "ID", 45), ("codigo", "Código", 110), ("nombre", "Producto", 260),
            ("categoria", "Categoría", 120), ("precio", "Precio (MXN)", 100), ("stock", "Stock", 70)
        ]:
            self.tree_busq.heading(col, text=txt)
            self.tree_busq.column(col, width=w)
        self.tree_busq.pack(fill="x", pady=4)

        # Controles del carrito
        mid = ttk.Frame(tab)
        mid.pack(fill="x", pady=6)
        ttk.Label(mid, text="Cantidad:").pack(side="left")
        self.spin_cant = ttk.Spinbox(mid, from_=1, to=999, width=6)
        self.spin_cant.set(1)
        self.spin_cant.pack(side="left", padx=5)
        ttk.Button(mid, text="Agregar al Carrito", command=self.agregar_al_carrito).pack(side="left", padx=4)
        ttk.Button(mid, text="Quitar Seleccionado", command=self.quitar_del_carrito).pack(side="left", padx=4)
        ttk.Button(mid, text="Vaciar Carrito", command=self.vaciar_carrito).pack(side="right")

        # Tabla del carrito
        cols_car = ("nombre", "cantidad", "precio", "subtotal")
        self.tree_carrito = ttk.Treeview(tab, columns=cols_car, show="headings", height=7)
        for col, txt, w in [
            ("nombre", "Producto", 320), ("cantidad", "Cant.", 80),
            ("precio", "P. Unitario", 110), ("subtotal", "Subtotal", 110)
        ]:
            self.tree_carrito.heading(col, text=txt)
            self.tree_carrito.column(col, width=w)
        self.tree_carrito.pack(fill="both", expand=True, pady=4)

        # Panel de cobro
        bot = ttk.Frame(tab)
        bot.pack(fill="x", pady=8)

        self.lbl_total = ttk.Label(bot, text="Total: $0.00 MXN", font=("Segoe UI", 14, "bold"))
        self.lbl_total.pack(side="left")

        metodos = self.conn.execute(
            "SELECT id, nombre, es_efectivo FROM metodos_pago WHERE activo = 1"
        ).fetchall()
        self.metodos_map = {m["nombre"]: (m["id"], m["es_efectivo"]) for m in metodos}

        ttk.Button(bot, text="Cobrar y Generar Ticket", command=self.procesar_cobro).pack(side="right", padx=5)
        self.entry_recibido = ttk.Entry(bot, width=12)
        self.entry_recibido.pack(side="right", padx=5)
        ttk.Label(bot, text="Recibido (MXN):").pack(side="right")

        self.combo_pago = ttk.Combobox(
            bot, values=list(self.metodos_map.keys()), state="readonly", width=22
        )
        if self.metodos_map:
            self.combo_pago.current(0)
        self.combo_pago.pack(side="right", padx=10)
        ttk.Label(bot, text="Método:").pack(side="right")

        self.buscar_para_venta()

    def buscar_para_venta(self):
        texto = self.entry_buscar_venta.get().strip()
        for item in self.tree_busq.get_children():
            self.tree_busq.delete(item)
        productos = buscar_producto(self.conn, texto) if texto else listar_productos(self.conn)
        for p in productos:
            self.tree_busq.insert("", "end", values=(
                p["id"], p["codigo_barras"], p["nombre"], p["categoria"],
                f"${p['precio']:.2f}", p["stock"]
            ))

    def agregar_al_carrito(self):
        sel = self.tree_busq.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona un producto de la lista.")
            return
        vals = self.tree_busq.item(sel[0], "values")
        pid, nombre = int(vals[0]), vals[2]
        precio = float(vals[4].replace("$", ""))
        try:
            cant = int(self.spin_cant.get())
            if cant <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Cantidad inválida.")
            return

        for item in self.carrito:
            if item["id"] == pid:
                item["cantidad"] += cant
                self.actualizar_vista_carrito()
                return

        self.carrito.append({"id": pid, "nombre": nombre, "precio": precio, "cantidad": cant})
        self.actualizar_vista_carrito()

    def quitar_del_carrito(self):
        sel = self.tree_carrito.selection()
        if not sel:
            return
        idx = self.tree_carrito.index(sel[0])
        self.carrito.pop(idx)
        self.actualizar_vista_carrito()

    def vaciar_carrito(self):
        self.carrito.clear()
        self.actualizar_vista_carrito()

    def actualizar_vista_carrito(self):
        for item in self.tree_carrito.get_children():
            self.tree_carrito.delete(item)

        divisa = self.combo_divisa.get()
        tasa = TASAS_CAMBIO.get(divisa, 1.0)
        total_mxn = 0.0

        for c in self.carrito:
            sub_mxn = c["precio"] * c["cantidad"]
            total_mxn += sub_mxn
            self.tree_carrito.insert("", "end", values=(
                c["nombre"], c["cantidad"],
                f"{c['precio'] * tasa:.2f}", f"{sub_mxn * tasa:.2f}"
            ))

        total_conv = total_mxn * tasa
        self.lbl_total.config(
            text=f"Total: {total_conv:,.2f} {divisa}  (Base: ${total_mxn:,.2f} MXN)"
        )

    def procesar_cobro(self):
        if not self.carrito:
            messagebox.showwarning("Atención", "El carrito está vacío.")
            return
        if self.usuario["rol"] != "cajero":
            messagebox.showwarning(
                "Permiso de Rol",
                "Por política del sistema, solo el rol CAJERO puede registrar ventas."
            )
            return

        metodo_nombre = self.combo_pago.get()
        metodo_id, es_efectivo = self.metodos_map[metodo_nombre]

        monto_recibido = None
        if es_efectivo:
            try:
                monto_recibido = float(self.entry_recibido.get())
            except ValueError:
                messagebox.showerror("Error", "Ingresa el monto recibido en efectivo (MXN).")
                return

        items = [(c["id"], c["cantidad"]) for c in self.carrito]
        carrito_copia = list(self.carrito)
        try:
            res = registrar_venta(self.conn, self.usuario, items, metodo_id, monto_recibido)
            self.mostrar_ticket(res, carrito_copia, metodo_nombre)
            self.vaciar_carrito()
            self.entry_recibido.delete(0, "end")
            self.ejecutar_actualizacion_global(silencioso=True)
        except (VentaError, auth.PermisoError) as e:
            messagebox.showerror("Error en venta", str(e))

    def mostrar_ticket(self, res, carrito_items, metodo):
        win = tk.Toplevel(self)
        win.title(f"Ticket {res['folio']}")
        win.geometry("380x460")

        divisa = self.combo_divisa.get()
        tasa = TASAS_CAMBIO.get(divisa, 1.0)
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lineas = [
            "========================================",
            "          GLOBAL RETAIL POS             ",
            "========================================",
            f"Folio:   {res['folio']}",
            f"Fecha:   {fecha}",
            f"Cajero:  {self.usuario['nombre']}",
            f"Método:  {metodo}",
            "----------------------------------------",
            f"{'Producto':<20} {'Cant':>4} {'Subtotal':>12}",
            "----------------------------------------",
        ]
        for c in carrito_items:
            sub = c["precio"] * c["cantidad"]
            lineas.append(f"{c['nombre'][:20]:<20} {c['cantidad']:>4} ${sub:>11.2f}")

        lineas.extend([
            "----------------------------------------",
            f"TOTAL (MXN):             ${res['total']:>11.2f}",
            f"EQUIVALENTE ({divisa[:3]}):      {res['total']*tasa:>11.2f}",
            f"CAMBIO (MXN):            ${res['cambio']:>11.2f}",
            "========================================",
            "       ¡Gracias por su compra!          ",
        ])
        if res["advertencias"]:
            lineas.append("\n[ALERTAS DE INVENTARIO]")
            lineas.extend(res["advertencias"])

        txt = tk.Text(win, font=("Consolas", 10), padx=10, pady=10)
        txt.insert("1.0", "\n".join(lineas))
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        ttk.Button(win, text="Cerrar Ticket", command=win.destroy).pack(pady=8)

    # =========================================================================
    # PESTAÑA 2: CATÁLOGO DE PRODUCTOS (CRUD COMPLETO)
    # =========================================================================
    def crear_tab_productos(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Catálogo de Productos")

        # Filtros
        filtros = ttk.Frame(tab)
        filtros.pack(fill="x", pady=(0, 8))
        ttk.Label(filtros, text="Buscar:").pack(side="left")
        self.filtro_prod = ttk.Entry(filtros, width=25)
        self.filtro_prod.pack(side="left", padx=5)
        self.filtro_prod.bind("<KeyRelease>", lambda e: self.cargar_tabla_productos())

        self.var_ver_inactivos = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filtros, text="Mostrar inactivos", variable=self.var_ver_inactivos,
            command=self.cargar_tabla_productos
        ).pack(side="left", padx=10)

        ttk.Button(filtros, text="Editar Seleccionado", command=self.abrir_editor_producto).pack(side="right", padx=4)
        if self.usuario["rol"] == "administrador":
            ttk.Button(filtros, text="Desactivar", command=self.desactivar_prod_sel).pack(side="right", padx=4)

        # Tabla
        cols = ("id", "codigo", "nombre", "categoria", "precio", "stock", "minimo", "activo")
        self.tree_prod = ttk.Treeview(tab, columns=cols, show="headings", height=13)
        self.tree_prod.tag_configure("bajo", background="#ffdddd")
        self.tree_prod.tag_configure("inactivo", foreground="gray")

        for col, txt, w in [
            ("id", "ID", 45), ("codigo", "Código", 110), ("nombre", "Nombre", 230),
            ("categoria", "Categoría", 120), ("precio", "Precio", 90),
            ("stock", "Stock", 70), ("minimo", "Mín.", 60), ("activo", "Estado", 80)
        ]:
            self.tree_prod.heading(col, text=txt)
            self.tree_prod.column(col, width=w)
        self.tree_prod.pack(fill="both", expand=True, pady=(0, 10))

        # Formulario de alta
        form = ttk.LabelFrame(tab, text="Registrar Nuevo Producto", padding=10)
        form.pack(fill="x")

        self.p_codigo = ttk.Entry(form, width=15)
        self.p_nombre = ttk.Entry(form, width=24)
        self.p_cat = ttk.Entry(form, width=16)
        self.p_precio = ttk.Entry(form, width=10)
        self.p_min = ttk.Entry(form, width=8)
        self.p_min.insert(0, "5")

        ttk.Label(form, text="Código:").grid(row=0, column=0, padx=4)
        self.p_codigo.grid(row=0, column=1, padx=4)
        ttk.Label(form, text="Nombre:").grid(row=0, column=2, padx=4)
        self.p_nombre.grid(row=0, column=3, padx=4)
        ttk.Label(form, text="Categoría:").grid(row=0, column=4, padx=4)
        self.p_cat.grid(row=0, column=5, padx=4)

        ttk.Label(form, text="Precio:").grid(row=1, column=0, padx=4, pady=6)
        self.p_precio.grid(row=1, column=1, padx=4, pady=6)
        ttk.Label(form, text="Stock Mín:").grid(row=1, column=2, padx=4, pady=6)
        self.p_min.grid(row=1, column=3, padx=4, pady=6)

        ttk.Button(form, text="Guardar Producto", command=self.guardar_producto).grid(row=1, column=4, columnspan=2, sticky="ew", padx=5)

        self.cargar_tabla_productos()

    def cargar_tabla_productos(self):
        for item in self.tree_prod.get_children():
            self.tree_prod.delete(item)
        texto = self.filtro_prod.get().strip().lower() if hasattr(self, "filtro_prod") else ""
        incluir = self.var_ver_inactivos.get() if hasattr(self, "var_ver_inactivos") else True

        for p in listar_productos(self.conn, incluir_inactivos=incluir):
            if texto and texto not in p["nombre"].lower() and texto not in p["codigo_barras"].lower():
                continue
            estado = "Activo" if p["activo"] else "Inactivo"
            tag = ()
            if not p["activo"]:
                tag = ("inactivo",)
            elif p["stock"] <= p["stock_minimo"]:
                tag = ("bajo",)

            self.tree_prod.insert("", "end", values=(
                p["id"], p["codigo_barras"], p["nombre"], p["categoria"],
                f"${p['precio']:.2f}", p["stock"], p["stock_minimo"], estado
            ), tags=tag)

    def guardar_producto(self):
        try:
            crear_producto(
                self.conn, self.usuario,
                self.p_codigo.get(), self.p_nombre.get(), self.p_cat.get(),
                float(self.p_precio.get()), int(self.p_min.get())
            )
            messagebox.showinfo("Éxito", "Producto registrado correctamente.")
            for e in (self.p_codigo, self.p_nombre, self.p_cat, self.p_precio):
                e.delete(0, "end")
            self.ejecutar_actualizacion_global(silencioso=True)
        except (ProductoError, ValueError, auth.PermisoError) as e:
            messagebox.showerror("Error", str(e))

    def abrir_editor_producto(self):
        sel = self.tree_prod.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona un producto para editar.")
            return
        vals = self.tree_prod.item(sel[0], "values")
        pid = int(vals[0])

        win = tk.Toplevel(self)
        win.title(f"Editar Producto #{pid}")
        win.geometry("340x240")
        frm = ttk.Frame(win, padding=15)
        frm.pack(fill="both", expand=True)

        e_nom = ttk.Entry(frm, width=25)
        e_nom.insert(0, vals[2])
        e_cat = ttk.Entry(frm, width=25)
        e_cat.insert(0, vals[3])
        e_pre = ttk.Entry(frm, width=25)
        e_pre.insert(0, vals[4].replace("$", ""))
        e_min = ttk.Entry(frm, width=25)
        e_min.insert(0, vals[6])

        for i, (lbl, ent) in enumerate([
            ("Nombre:", e_nom), ("Categoría:", e_cat),
            ("Precio:", e_pre), ("Stock Mínimo:", e_min)
        ]):
            ttk.Label(frm, text=lbl).grid(row=i, column=0, sticky="e", padx=5, pady=5)
            ent.grid(row=i, column=1, pady=5)

        def guardar_cambios():
            try:
                modificar_producto(
                    self.conn, self.usuario, pid,
                    nombre=e_nom.get(), categoria=e_cat.get(),
                    precio=float(e_pre.get()), stock_minimo=int(e_min.get())
                )
                messagebox.showinfo("Actualizado", "Producto modificado correctamente.")
                win.destroy()
                self.ejecutar_actualizacion_global(silencioso=True)
            except (ProductoError, ValueError, auth.PermisoError) as e:
                messagebox.showerror("Error", str(e))

        ttk.Button(frm, text="Guardar Cambios", command=guardar_cambios).grid(
            row=4, column=0, columnspan=2, pady=15, sticky="ew"
        )

    def desactivar_prod_sel(self):
        sel = self.tree_prod.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona un producto.")
            return
        pid = int(self.tree_prod.item(sel[0], "values")[0])
        try:
            desactivar_producto(self.conn, self.usuario, pid)
            messagebox.showinfo("Éxito", "Producto desactivado.")
            self.ejecutar_actualizacion_global(silencioso=True)
        except (ProductoError, auth.PermisoError) as e:
            messagebox.showerror("Error", str(e))

    # =========================================================================
    # PESTAÑA 3: CARGA MASIVA CSV
    # =========================================================================
    def crear_tab_carga_masiva(self):
        tab = ttk.Frame(self.notebook, padding=25)
        self.notebook.add(tab, text="Carga Masiva (CSV)")

        ttk.Label(
            tab, text="Importación Masiva de Inventario", font=("Segoe UI", 14, "bold")
        ).pack(pady=(0, 10))
        ttk.Label(
            tab,
            text="El archivo CSV debe incluir el encabezado exacto:\n"
                 "codigo_barras,nombre,categoria,precio,stock,stock_minimo",
            justify="center"
        ).pack(pady=10)

        btns = ttk.Frame(tab)
        btns.pack(pady=15)
        ttk.Button(btns, text="Seleccionar CSV e Importar", command=self.importar_csv).pack(side="left", padx=10)
        ttk.Button(btns, text="Generar Plantilla CSV de Ejemplo", command=self.exportar_plantilla_csv).pack(side="left", padx=10)

    def importar_csv(self):
        ruta = filedialog.askopenfilename(filetypes=[("Archivos CSV", "*.csv")])
        if not ruta:
            return
        try:
            cant = cargar_inventario_masivo(self.conn, self.usuario, ruta)
            messagebox.showinfo("Importación Exitosa", f"Se importaron {cant} productos correctamente.")
            self.ejecutar_actualizacion_global(silencioso=True)
        except (InventarioError, auth.PermisoError) as e:
            messagebox.showerror("Error en importación", str(e))

    def exportar_plantilla_csv(self):
        ruta = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="plantilla_productos.csv",
            filetypes=[("Archivos CSV", "*.csv")]
        )
        if not ruta:
            return
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["codigo_barras", "nombre", "categoria", "precio", "stock", "stock_minimo"])
            writer.writerow(["759001", "Producto Ejemplo 1", "Abarrotes", 25.50, 30, 5])
        messagebox.showinfo("Plantilla Lista", f"Plantilla guardada en:\n{ruta}")

    # =========================================================================
    # PESTAÑA 4: PEDIDOS Y RECEPCIÓN DE MERCANCÍA
    # =========================================================================
    def crear_tab_recepcion(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Pedidos y Recepción")

        f_crear = ttk.LabelFrame(tab, text="Crear Pedido a Proveedor", padding=10)
        f_crear.pack(fill="x", pady=5)

        ttk.Label(f_crear, text="ID Producto:").pack(side="left", padx=5)
        self.rec_pid = ttk.Entry(f_crear, width=8)
        self.rec_pid.pack(side="left", padx=5)
        ttk.Label(f_crear, text="Cantidad pedida:").pack(side="left", padx=5)
        self.rec_cant = ttk.Entry(f_crear, width=8)
        self.rec_cant.pack(side="left", padx=5)
        ttk.Button(f_crear, text="Registrar Pedido", command=self.accion_crear_pedido).pack(side="left", padx=10)

        f_conf = ttk.LabelFrame(tab, text="Confirmar Recepción de Mercancía", padding=10)
        f_conf.pack(fill="x", pady=10)

        ttk.Label(f_conf, text="ID Pedido:").pack(side="left", padx=5)
        self.conf_pedido_id = ttk.Entry(f_conf, width=8)
        self.conf_pedido_id.pack(side="left", padx=5)
        ttk.Label(f_conf, text="ID Producto:").pack(side="left", padx=5)
        self.conf_prod_id = ttk.Entry(f_conf, width=8)
        self.conf_prod_id.pack(side="left", padx=5)
        ttk.Label(f_conf, text="Cant. Recibida:").pack(side="left", padx=5)
        self.conf_cant_rec = ttk.Entry(f_conf, width=8)
        self.conf_cant_rec.pack(side="left", padx=5)
        ttk.Button(f_conf, text="Confirmar Recepción", command=self.accion_confirmar_recepcion).pack(side="left", padx=10)

        # Lista de pedidos recientes
        ttk.Label(tab, text="Pedidos Registrados:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 4))
        self.tree_pedidos = ttk.Treeview(tab, columns=("id", "fecha", "estado"), show="headings", height=10)
        for col, txt, w in [("id", "ID Pedido", 100), ("fecha", "Fecha", 200), ("estado", "Estado", 150)]:
            self.tree_pedidos.heading(col, text=txt)
            self.tree_pedidos.column(col, width=w)
        self.tree_pedidos.pack(fill="both", expand=True)
        self.cargar_pedidos()

    def cargar_pedidos(self):
        for item in self.tree_pedidos.get_children():
            self.tree_pedidos.delete(item)
        try:
            filas = self.conn.execute("SELECT id, fecha, estado FROM pedidos ORDER BY id DESC").fetchall()
            for r in filas:
                self.tree_pedidos.insert("", "end", values=(r["id"], r["fecha"], r["estado"]))
        except Exception:
            pass

    def accion_crear_pedido(self):
        try:
            pid = int(self.rec_pid.get())
            cant = int(self.rec_cant.get())
            pedido_id = crear_pedido(self.conn, self.usuario, [(pid, cant)])
            messagebox.showinfo("Pedido Creado", f"Se registró el pedido #{pedido_id}.")
            self.cargar_pedidos()
        except (RecepcionError, ValueError, auth.PermisoError) as e:
            messagebox.showerror("Error", str(e))

    def accion_confirmar_recepcion(self):
        try:
            pedido_id = int(self.conf_pedido_id.get())
            prod_id = int(self.conf_prod_id.get())
            cant_rec = int(self.conf_cant_rec.get())
            recibidas = {prod_id: cant_rec}

            try:
                confirmar_recepcion(self.conn, self.usuario, pedido_id, recibidas)
                messagebox.showinfo("Éxito", "Recepción confirmada sin discrepancias.")
                self.cargar_pedidos()
                self.ejecutar_actualizacion_global(silencioso=True)
            except DiscrepanciaPendiente as d:
                detalles = "\n".join(
                    f"- {x['nombre']}: pedido {x['pedida']}, recibido {x['recibida']} (dif: {x['diferencia']})"
                    for x in d.diferencias
                )
                aceptar = messagebox.askyesno(
                    "Discrepancias Detectadas",
                    f"Existen diferencias:\n{detalles}\n\n¿Aceptar discrepancias y actualizar inventario?"
                )
                if aceptar:
                    confirmar_recepcion(self.conn, self.usuario, pedido_id, recibidas, discrepancias_aceptadas=True)
                    messagebox.showinfo("Éxito", "Recepción confirmada con discrepancias registradas.")
                    self.cargar_pedidos()
                    self.ejecutar_actualizacion_global(silencioso=True)
        except (RecepcionError, ValueError, auth.PermisoError) as e:
            messagebox.showerror("Error", str(e))

    # =========================================================================
    # PESTAÑA 5: HISTORIAL DE VENTAS Y REPORTES
    # =========================================================================
    def crear_tab_historial_ventas(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Historial de Ventas")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Registro Histórico de Ventas", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(top, text="Exportar Reporte CSV", command=self.exportar_reporte_ventas).pack(side="right")

        cols = ("folio", "fecha", "total")
        self.tree_ventas = ttk.Treeview(tab, columns=cols, show="headings", height=16)
        for col, txt, w in [("folio", "Folio", 180), ("fecha", "Fecha y Hora", 220), ("total", "Total (MXN)", 140)]:
            self.tree_ventas.heading(col, text=txt)
            self.tree_ventas.column(col, width=w)
        self.tree_ventas.pack(fill="both", expand=True)

        self.cargar_historial_ventas()

    def cargar_historial_ventas(self):
        for item in self.tree_ventas.get_children():
            self.tree_ventas.delete(item)
        filas = self.conn.execute("SELECT folio, fecha, total FROM ventas ORDER BY id DESC").fetchall()
        for r in filas:
            self.tree_ventas.insert("", "end", values=(r["folio"], r["fecha"], f"${r['total']:,.2f}"))

    def exportar_reporte_ventas(self):
        ruta = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="reporte_ventas.csv",
            filetypes=[("Archivos CSV", "*.csv")]
        )
        if not ruta:
            return
        filas = self.conn.execute("SELECT folio, fecha, total FROM ventas ORDER BY id DESC").fetchall()
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["folio", "fecha", "total_mxn"])
            for r in filas:
                writer.writerow([r["folio"], r["fecha"], r["total"]])
        messagebox.showinfo("Exportado", f"Reporte guardado en:\n{ruta}")

    # =========================================================================
    # PESTAÑA 6: ADMINISTRACIÓN (USUARIOS, RESPALDOS, SINCRONIZACIÓN)
    # =========================================================================
    def crear_tab_admin(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Administración")

        # 1. Respaldos y Sync
        f_ops = ttk.LabelFrame(tab, text="Respaldos y Sincronización Corporativa", padding=10)
        f_ops.pack(fill="x", pady=5)

        ttk.Button(f_ops, text="Generar Respaldo Manual", command=self.accion_respaldo).pack(side="left", padx=5)
        ttk.Separator(f_ops, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Label(f_ops, text="Código:").pack(side="left", padx=4)
        self.sync_cod = ttk.Entry(f_ops, width=14)
        self.sync_cod.pack(side="left", padx=4)
        ttk.Label(f_ops, text="Nuevo precio:").pack(side="left", padx=4)
        self.sync_precio = ttk.Entry(f_ops, width=10)
        self.sync_precio.pack(side="left", padx=4)
        ttk.Button(f_ops, text="Sincronizar Precio", command=self.accion_sincronizar).pack(side="left", padx=8)

        # 2. Gestión de Usuarios
        f_usr = ttk.LabelFrame(tab, text="Alta de Nuevo Usuario", padding=10)
        f_usr.pack(fill="x", pady=10)

        self.u_cod = ttk.Entry(f_usr, width=14)
        self.u_nom = ttk.Entry(f_usr, width=20)
        self.u_pas = ttk.Entry(f_usr, width=14, show="*")
        self.u_rol = ttk.Combobox(
            f_usr, values=["administrador", "cajero", "encargado_inventario"], state="readonly", width=20
        )
        self.u_rol.current(1)

        ttk.Label(f_usr, text="Usuario:").grid(row=0, column=0, padx=4)
        self.u_cod.grid(row=0, column=1, padx=4)
        ttk.Label(f_usr, text="Nombre:").grid(row=0, column=2, padx=4)
        self.u_nom.grid(row=0, column=3, padx=4)
        ttk.Label(f_usr, text="Contraseña:").grid(row=0, column=4, padx=4)
        self.u_pas.grid(row=0, column=5, padx=4)
        ttk.Label(f_usr, text="Rol:").grid(row=0, column=6, padx=4)
        self.u_rol.grid(row=0, column=7, padx=4)
        ttk.Button(f_usr, text="Crear Usuario", command=self.accion_crear_usuario).grid(row=0, column=8, padx=8)

        self.tree_usr = ttk.Treeview(tab, columns=("codigo", "nombre", "rol"), show="headings", height=9)
        for col, txt, w in [("codigo", "Usuario", 140), ("nombre", "Nombre Completo", 260), ("rol", "Rol", 180)]:
            self.tree_usr.heading(col, text=txt)
            self.tree_usr.column(col, width=w)
        self.tree_usr.pack(fill="both", expand=True, pady=5)
        self.cargar_usuarios()

    def cargar_usuarios(self):
        for item in self.tree_usr.get_children():
            self.tree_usr.delete(item)
        filas = self.conn.execute("SELECT codigo, nombre, rol FROM usuarios ORDER BY id ASC").fetchall()
        for r in filas:
            self.tree_usr.insert("", "end", values=(r["codigo"], r["nombre"], r["rol"]))

    def accion_crear_usuario(self):
        try:
            auth.crear_usuario(
                self.conn, self.u_cod.get().strip().lower(),
                self.u_nom.get().strip(), self.u_pas.get(), self.u_rol.get()
            )
            messagebox.showinfo("Éxito", "Usuario creado correctamente.")
            for e in (self.u_cod, self.u_nom, self.u_pas):
                e.delete(0, "end")
            self.cargar_usuarios()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def accion_respaldo(self):
        try:
            ruta = generar_respaldo(RUTA_BD, "backups")
            messagebox.showinfo("Respaldo Exitoso", f"Respaldo guardado en:\n{ruta}")
        except BackupError as e:
            messagebox.showerror("Error de respaldo", str(e))

    def accion_sincronizar(self):
        try:
            datos = [{"codigo_barras": self.sync_cod.get().strip(), "precio": float(self.sync_precio.get())}]
            n = sincronizar_precios(self.conn, self.usuario, datos)
            messagebox.showinfo("Sincronización", f"Se actualizó {n} producto correctamente.")
            self.ejecutar_actualizacion_global(silencioso=True)
        except (SyncError, ValueError, auth.PermisoError) as e:
            messagebox.showerror("Error", str(e))


if __name__ == "__main__":
    app = TiendaApp()
    app.mainloop()