# TicketSystemBack — API Central, Motor de Fidelización, Ciberseguridad WAF y Facturación Electrónica DIAN

Repositorio oficial del **Backend y Capa de Datos** de la plataforma **ConfyTickets (TicketSystem)**, desarrollado en **Python 3.11 + FastAPI + SQLAlchemy 2.0 (Async) + PostgreSQL 15 + Redis 7**.

---

## 1. Arquitectura y Responsabilidades del Servicio

Este servicio actúa como el núcleo transaccional (*Stateless API*) encargado de:

1. **Autenticación, RBAC y Recuperación OTP (`/api/v1/auth`):**
   - Registro e inicio de sesión de clientes y personal operativo (`OWNER`, `GENERAL_ADMIN`, `STORE_ADMIN`, `CASHIER`).
   - Contraseñas cifradas con **Bcrypt** y tokens **JWT (`HS256`)** con validación estricta de permisos por dependencia (`Depends(get_current_user)`, `Depends(get_current_cashier_or_admin)`, `Depends(get_current_admin)`).
   - Recuperación de contraseña mediante **código OTP de 6 dígitos** enviado por correo electrónico y almacenado en **Redis** (TTL 15 minutos, *cooldown* de 60 segundos, máximo 5 intentos).
   - Registro auditable de consentimientos **Habeas Data (Ley 1581 de 2012 Colombia)** con captura de IP, User-Agent y versión de política.

2. **Motor Matemático de Fidelización de 8 Sellos (`/api/v1/cashier`, `/api/v1/me`):**
   - Cálculo parametrizable de sellos: $\text{sellos} = \lfloor \text{monto\_elegible} / 10000 \rfloor$.
   - Libro mayor inmutable de movimientos (`stamp_movements`: `EARN`, `CARRY_IN`, `CARRY_OUT`, `REVERSAL`, `ADJUSTMENT`, `EXPIRATION`).
   - **Arrastre Atómico (*Carry-Over*):** Cuando una compra supera los sellos faltantes para completar los 8 sellos, cierra la tarjeta actual, emite automáticamente la recompensa de cortesía, abre el ciclo $N+1$ y acredita los sellos excedentes en una única transacción ACID protegida con bloqueo pesimista (`SELECT ... FOR UPDATE`).
   - Soporte dual de lectura en caja: **QR Efímero de un solo uso (`SHA-256`, vigencia 60s-300s)** y **Carnet QR Permanente de Socio (`cfy_member_<uuid>`)** estilo Starbucks Rewards.

3. **Facturación Electrónica DIAN — Anexo Técnico 1.9 / UBL 2.1 (`/api/v1/admin/invoices`):**
   - Cálculo determinístico del **CUFE (Código Único de Factura Electrónica)** y **CUDE (Nota Crédito)** mediante algoritmo oficial **SHA-384**.
   - Desglose tributario automático para cafeterías y restaurantes en Colombia (**Base Gravable + Impuesto Nacional al Consumo INC 8%**).
   - Generación y descarga directa del archivo **XML estándar UBL 2.1 (`Invoice-2`)** con extensión `sts:DianExtensions` listo para transmisión a la DIAN o proveedor tecnológico.
   - Control auditable de reimpresiones de tirilla POS (`Original` vs. `Reimpresión #N`) y emisión de **Notas Crédito Electrónicas (`NC`)** al anular compras con reversión automática de sellos.

4. **Escudo de Ciberseguridad (`app/core/security_middleware.py`):**
   - **Contención Anti-DDoS (Rate Limiting en Redis):** Ventana deslizante de **120 peticiones/minuto** globales por IP y **15 peticiones/minuto** en endpoints de autenticación, más bloqueo temporal de cuenta por 5 minutos tras 5 contraseñas fallidas consecutivas.
   - **Inspector WAF Anti-SQLi y Anti-XSS:** Bloqueo preventivo (`400 Bad Request` · `WAF_SQLI_XSS_BLOCKED`) ante firmas de inyección SQL (`UNION SELECT`, `OR 1=1`, `DROP TABLE`, `pg_sleep`) o scripts en parámetros y cuerpos JSON.
   - **Cabeceras HTTP de Seguridad:** `Content-Security-Policy (CSP)`, `Strict-Transport-Security (HSTS)`, `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff` y `Permissions-Policy`.

---

## 2. Estructura del Repositorio

```text
TicketSystemBack/
├── app/
│   ├── api/
│   │   ├── deps.py                     # Dependencias JWT y guardias RBAC por rol
│   │   └── v1/
│   │       ├── auth.py                 # Login, Registro, Staff, OTP, Cambio de Clave, Security Status
│   │       ├── me.py                   # Perfil cliente, Tarjetas, QR en vivo, Mis Facturas DIAN
│   │       ├── cashier.py              # Registro de ventas POS, Canje QR y Canje Asistido
│   │       ├── admin.py                # KPIs, Bitácora, Sedes, Productos, Banner, Clientes, DIAN, SQL
│   │       └── router.py               # Enrutador maestro /api/v1
│   ├── core/
│   │   ├── config.py                   # Configuración Pydantic Settings
│   │   ├── database.py                 # Motor SQLAlchemy 2.0 AsyncPG
│   │   ├── redis.py                    # Pool asíncrono Redis 7
│   │   ├── security.py                 # Hashing Bcrypt y firma JWT
│   │   └── security_middleware.py      # Middleware WAF Anti-SQLi/XSS, Rate Limit y Cabeceras HTTP
│   ├── models/                         # Modelos ORM SQLAlchemy (User, Business, Loyalty, Purchase, DIAN)
│   ├── schemas/                        # Esquemas Pydantic v2 de validación de entrada/salida
│   ├── services/
│   │   ├── loyalty_service.py          # Motor transaccional de 8 sellos, Carry-Over, Anulación y Ajustes
│   │   ├── dian_invoice_service.py     # Motor de Facturación Electrónica DIAN (CUFE SHA-384 y XML UBL 2.1)
│   │   ├── qr_service.py               # Emisión y consumo atómico de tokens QR (SHA-256)
│   │   └── email_service.py            # Despacho de correos HTML para recuperación OTP
│   └── main.py                         # Punto de entrada FastAPI
├── database/
│   ├── 01_schema_ddl.sql               # Esquema relacional base (24 tablas PostgreSQL 15)
│   └── 03_dian_invoicing.sql           # Tablas dian_settings y electronic_invoices
├── scripts/
│   ├── import_sql.py                   # Importador CLI transaccional seguro de archivos/sentencias SQL
│   ├── reset_password.py               # Script CLI de emergencia (Break-Glass) para restablecer claves
│   └── seed_db.py                      # Semilla inicial de negocio, sedes y programa de 8 sellos
├── Dockerfile                          # Contenedor Python 3.11-slim para producción
├── docker-compose.yml                  # Orquestación local (Postgres 5439, Redis 6389, Adminer 8085, API 8088)
├── requirements.txt                    # Dependencias Python fijadas
└── .env.example                        # Plantilla de variables de entorno
```

---

## 3. Puesta en Marcha Rápida (Docker Compose)

1. **Clonar el repositorio y configurar variables:**
   ```bash
   git clone https://github.com/Huesly/TicketSystemBack.git
   cd TicketSystemBack
   cp .env.example .env
   ```

2. **Levantar la infraestructura completa (`PostgreSQL 15` + `Redis 7` + `FastAPI` + `Adminer`):**
   ```bash
   docker compose up -d --build
   ```

3. **Inicializar datos base del negocio y sedes (primera vez):**
   ```bash
   docker exec confytickets-backend python /app/scripts/seed_db.py
   ```

4. **Puertos expuestos:**
   - **API REST FastAPI:** `http://localhost:8088`
   - **Documentación Interactiva Swagger / OpenAPI:** `http://localhost:8088/docs`
   - **Estado del Escudo de Ciberseguridad:** `http://localhost:8088/api/v1/auth/security-status`
   - **PostgreSQL 15:** `localhost:5439`
   - **Redis 7:** `localhost:6389`
   - **Adminer (Visor Web SQL):** `http://localhost:8085`

---

## 4. Herramientas CLI de Administración y Soporte

### A. Importador Seguro de Inputs SQL (`scripts/import_sql.py`)
Ejecuta lotes o archivos `.sql` dentro de una transacción atómica con `ROLLBACK` automático ante cualquier error y bloqueo de comandos destructivos (`DROP DATABASE`, `TRUNCATE` maestro, `COPY PROGRAM`):

```bash
# Modo Simulación (Dry-Run: valida y muestra resultados pero revierte con ROLLBACK)
docker exec confytickets-backend python /app/scripts/import_sql.py --sql "SELECT code, name, city FROM stores;" --dry-run

# Importar un archivo .sql confirmando cambios (COMMIT)
docker exec confytickets-backend python /app/scripts/import_sql.py /app/database/03_dian_invoicing.sql
```

### B. Recuperación de Contraseña por Consola (`scripts/reset_password.py`)
En caso de contingencia sin acceso a correo SMTP, permite restablecer la contraseña de cualquier administrador o usuario con hash Bcrypt y desbloquear la cuenta:

```bash
docker exec confytickets-backend python /app/scripts/reset_password.py admin@tudominio.com "NuevaClaveSegura2026*"
```

---

## 5. Resumen de Endpoints Principales (`/api/v1`)

| Método | Ruta | Permiso | Descripción |
| :---: | :--- | :---: | :--- |
| `POST` | `/api/v1/auth/login` | Público | Autenticación JWT con protección anti-fuerza bruta en Redis |
| `POST` | `/api/v1/auth/register` | Público | Registro de cliente + tarjeta Ciclo 1 + consentimiento Ley 1581 |
| `POST` | `/api/v1/auth/register-staff` | Público/Admin | Registro de personal (`GENERAL_ADMIN`, `STORE_ADMIN`, `CASHIER`) |
| `POST` | `/api/v1/auth/forgot-password` | Público | Emisión de código OTP de 6 dígitos en Redis (TTL 15 min) |
| `POST` | `/api/v1/auth/reset-password` | Público | Validación de OTP y actualización de contraseña con Bcrypt |
| `POST` | `/api/v1/auth/change-password` | JWT | Cambio de contraseña desde sesión activa |
| `GET` | `/api/v1/auth/security-status` | Público | Diagnóstico en vivo del WAF, Rate Limiter, cabeceras y Habeas Data |
| `GET` | `/api/v1/me` / `PATCH /api/v1/me` | Cliente JWT | Perfil del cliente y documento tributario DIAN (`CC`/`NIT`) |
| `GET` | `/api/v1/me/purchases` | Cliente JWT | Historial de compras y facturas electrónicas del cliente |
| `GET` | `/api/v1/me/purchases/{id}/invoice` | Cliente JWT | Representación gráfica DIAN (CUFE) y reimpresión para el cliente |
| `GET` | `/api/v1/me/purchases/{id}/invoice-xml` | Cliente JWT | Descarga de archivo XML UBL 2.1 DIAN del cliente |
| `POST` | `/api/v1/cashier/purchases` | Cajero/Admin | Registrar compra en POS, calcular sellos (carry-over) y emitir factura DIAN |
| `POST` | `/api/v1/cashier/lookup-member` | Cajero/Admin | Escanear carnet QR permanente o temporal del socio |
| `POST` | `/api/v1/cashier/redeem-member-reward` | Cajero/Admin | Canje asistido de bebida de cortesía en barra |
| `GET` | `/api/v1/admin/invoices/{purchase_id}` | Cajero/Admin | Obtener/reimprimir Factura Electrónica DIAN con contador de copias |
| `GET` | `/api/v1/admin/invoices/{purchase_id}/xml` | Cajero/Admin | Descargar XML UBL 2.1 oficial para la DIAN |
| `GET/PUT` | `/api/v1/admin/dian-settings` | Admin | Configurar NIT, Resolución DIAN, Prefijo, Rango, Clave Técnica e INC % |
| `POST` | `/api/v1/admin/purchases/{id}/cancel` | Cajero/Admin | Anular compra, emitir Nota Crédito DIAN (`CUDE`) y revertir sellos |
| `GET/PATCH` | `/api/v1/admin/customers` | Admin | Directorio de clientes, estado de cuenta y documento DIAN |
| `POST` | `/api/v1/admin/customers/{id}/adjust-stamps` | Admin | Ajuste manual auditado de sellos (`+` / `-`) con motivo obligatorio |
| `GET/PUT` | `/api/v1/admin/loyalty-rules` | Admin | Configurar valor en COP por sello y reglas del programa |
