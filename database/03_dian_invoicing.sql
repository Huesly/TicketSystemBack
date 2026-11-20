-- =============================================================================
-- MIGRACIÓN 03: FACTURACIÓN ELECTRÓNICA DIAN (ANEXO 1.9 / UBL 2.1) Y REIMPRESIÓN
-- =============================================================================

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS document_type VARCHAR(20) DEFAULT 'CC';
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS document_number VARCHAR(40) DEFAULT '222222222222';

CREATE TABLE IF NOT EXISTS dian_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
    nit_emisor VARCHAR(30) NOT NULL DEFAULT '901452890',
    dv_emisor VARCHAR(5) NOT NULL DEFAULT '3',
    razon_social VARCHAR(180) NOT NULL DEFAULT 'CONFYTICKETS CAFÉ DE ESPECIALIDAD S.A.S.',
    nombre_comercial VARCHAR(150) NOT NULL DEFAULT 'ConfyTickets Specialty Coffee',
    regimen_fiscal VARCHAR(100) NOT NULL DEFAULT 'Responsable del Impuesto Nacional al Consumo (INC 8%)',
    actividad_ciiu VARCHAR(30) NOT NULL DEFAULT '5613',
    resolucion_numero VARCHAR(50) NOT NULL DEFAULT '18764065891234',
    resolucion_fecha VARCHAR(20) NOT NULL DEFAULT '2026-01-15',
    resolucion_vigencia_meses INT NOT NULL DEFAULT 24,
    prefijo VARCHAR(15) NOT NULL DEFAULT 'SETT',
    rango_desde BIGINT NOT NULL DEFAULT 1,
    rango_hasta BIGINT NOT NULL DEFAULT 50000,
    consecutivo_actual BIGINT NOT NULL DEFAULT 1000,
    clave_tecnica VARCHAR(128) NOT NULL DEFAULT 'fc8eac422eba16e22ffd8c6f94b3f40a6e38162c',
    ambiente VARCHAR(5) NOT NULL DEFAULT '2', -- '1': Producción DIAN, '2': Habilitación/Pruebas
    tarifa_impoconsumo_pct INT NOT NULL DEFAULT 8,
    direccion_emisor VARCHAR(255) NOT NULL DEFAULT 'Carrera 35 # 48-12 Cabecera del Llano',
    municipio_emisor VARCHAR(80) NOT NULL DEFAULT 'Bucaramanga',
    departamento_emisor VARCHAR(80) NOT NULL DEFAULT 'Santander',
    codigo_municipio_dane VARCHAR(10) NOT NULL DEFAULT '68001',
    telefono_emisor VARCHAR(40) NOT NULL DEFAULT '+57 607 6438890',
    correo_emisor VARCHAR(150) NOT NULL DEFAULT 'facturacion@confytickets.co',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS electronic_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    purchase_id UUID NOT NULL UNIQUE REFERENCES purchases(id) ON DELETE CASCADE,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    customer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    invoice_number VARCHAR(50) NOT NULL,
    prefix VARCHAR(15) NOT NULL DEFAULT 'SETT',
    consecutive BIGINT NOT NULL DEFAULT 1,
    cufe VARCHAR(128) NOT NULL,
    qr_url VARCHAR(500) NOT NULL,
    issue_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payment_method VARCHAR(40) NOT NULL DEFAULT 'Efectivo',
    customer_doc_type VARCHAR(20) NOT NULL DEFAULT 'CC',
    customer_doc_number VARCHAR(40) NOT NULL DEFAULT '222222222222',
    customer_name VARCHAR(150) NOT NULL,
    customer_email VARCHAR(255) NOT NULL,
    subtotal_cop BIGINT NOT NULL,
    tax_type VARCHAR(30) NOT NULL DEFAULT 'INC',
    tax_rate_pct INT NOT NULL DEFAULT 8,
    tax_amount_cop BIGINT NOT NULL,
    discount_cop BIGINT NOT NULL DEFAULT 0,
    total_cop BIGINT NOT NULL,
    stamps_awarded INT NOT NULL DEFAULT 0,
    items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    dian_status VARCHAR(30) NOT NULL DEFAULT 'READY_FOR_DIAN',
    credit_note_number VARCHAR(50),
    credit_note_cude VARCHAR(128),
    cancellation_reason VARCHAR(500),
    reprint_count INT NOT NULL DEFAULT 0,
    last_reprinted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
