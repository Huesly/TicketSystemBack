-- =============================================================================
-- ESQUEMA DDL POSTGRESQL - PLATAFORMA DE FIDELIZACIÓN (CONFYTICKETS)
-- Versión: 1.0 (Producción / Migración Inicial)
-- Motor: PostgreSQL 15+
-- =============================================================================

-- Habilitar extensión para UUIDs criptográficos
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 1. ESTRUCTURA DE NEGOCIO Y MULTISEDES
-- =============================================================================

CREATE TABLE businesses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) NOT NULL,
    legal_name VARCHAR(180),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'archived')),
    default_currency CHAR(3) NOT NULL DEFAULT 'COP',
    timezone VARCHAR(50) NOT NULL DEFAULT 'America/Bogota',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE businesses IS 'Negocio principal o marca de la cadena de cafeterías';

CREATE TABLE stores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    code VARCHAR(30) NOT NULL,
    name VARCHAR(120) NOT NULL,
    address VARCHAR(255) NOT NULL,
    city VARCHAR(80) NOT NULL,
    department VARCHAR(80) NOT NULL,
    country CHAR(2) NOT NULL DEFAULT 'CO',
    latitude NUMERIC(10, 7),
    longitude NUMERIC(10, 7),
    geofence_radius_meters INTEGER NOT NULL DEFAULT 150 CHECK (geofence_radius_meters > 0),
    timezone VARCHAR(50) NOT NULL DEFAULT 'America/Bogota',
    opening_hours JSONB NOT NULL DEFAULT '{}'::jsonb,
    phone VARCHAR(30),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_id, code)
);

COMMENT ON TABLE stores IS 'Sedes físicas o sucursales de la cafetería';

-- =============================================================================
-- 2. USUARIOS, ROLES Y ACCESOS (INTEGRACIÓN SUPABASE AUTH)
-- =============================================================================

CREATE TABLE profiles (
    id UUID PRIMARY KEY, -- Referencia a auth.users(id) en Supabase
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    phone VARCHAR(30),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'blocked', 'suspended')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE profiles IS 'Perfiles de clientes y empleados sincronizados con la identidad';

CREATE TABLE business_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    role VARCHAR(30) NOT NULL CHECK (role IN ('OWNER', 'GENERAL_ADMIN', 'STORE_ADMIN', 'CASHIER', 'MARKETING')),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_id, user_id)
);

COMMENT ON TABLE business_memberships IS 'Asignación de roles de staff para la administración';

CREATE TABLE membership_store_assignments (
    membership_id UUID NOT NULL REFERENCES business_memberships(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (membership_id, store_id)
);

COMMENT ON TABLE membership_store_assignments IS 'Sedes autorizadas para cada cajero o administrador de sede';

-- =============================================================================
-- 3. PROGRAMAS DE FIDELIZACIÓN Y REGLAS VERSIONADAS
-- =============================================================================

CREATE TABLE loyalty_programs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    name VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE loyalty_program_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id UUID NOT NULL REFERENCES loyalty_programs(id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    stamps_required SMALLINT NOT NULL DEFAULT 8 CHECK (stamps_required > 0),
    minimum_purchase BIGINT NOT NULL DEFAULT 0 CHECK (minimum_purchase >= 0),
    amount_per_stamp BIGINT NOT NULL DEFAULT 10000 CHECK (amount_per_stamp > 0),
    max_stamps_per_purchase SMALLINT NOT NULL DEFAULT 3 CHECK (max_stamps_per_purchase >= 1),
    stamp_expiry_days INTEGER CHECK (stamp_expiry_days IS NULL OR stamp_expiry_days > 0),
    reward_expiry_days INTEGER CHECK (reward_expiry_days IS NULL OR reward_expiry_days > 0),
    effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_until TIMESTAMPTZ,
    rules_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (program_id, version_number)
);

CREATE TABLE program_stores (
    program_id UUID NOT NULL REFERENCES loyalty_programs(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (program_id, store_id)
);

-- =============================================================================
-- 4. TARJETAS DIGITALES, CICLOS Y MOVIMIENTOS DE SELLOS
-- =============================================================================

CREATE TABLE loyalty_cards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    program_id UUID NOT NULL REFERENCES loyalty_programs(id) ON DELETE RESTRICT,
    program_version_id UUID NOT NULL REFERENCES loyalty_program_versions(id),
    cycle_number INTEGER NOT NULL DEFAULT 1 CHECK (cycle_number >= 1),
    stamps_required_snapshot SMALLINT NOT NULL DEFAULT 8 CHECK (stamps_required_snapshot > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'accumulating' CHECK (status IN ('accumulating', 'completed', 'expired', 'cancelled')),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    UNIQUE (customer_id, program_id, cycle_number)
);

COMMENT ON TABLE loyalty_cards IS 'Ciclos de acumulación hacia la meta de 8 sellos de cada cliente';

-- =============================================================================
-- 5. COMPRAS Y CÓDIGOS QR
-- =============================================================================

CREATE TABLE purchases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    customer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    cashier_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    receipt_reference VARCHAR(100) NOT NULL,
    gross_amount BIGINT NOT NULL CHECK (gross_amount >= 0),
    discount_amount BIGINT NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    eligible_amount BIGINT NOT NULL CHECK (eligible_amount >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'COP',
    program_version_id UUID NOT NULL REFERENCES loyalty_program_versions(id),
    status VARCHAR(30) NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled', 'partially_refunded', 'refunded', 'under_review')),
    idempotency_key UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ,
    UNIQUE (business_id, store_id, receipt_reference)
);

COMMENT ON TABLE purchases IS 'Transacciones de compra registradas en caja';

CREATE TABLE stamp_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id UUID NOT NULL REFERENCES loyalty_cards(id) ON DELETE RESTRICT,
    purchase_id UUID REFERENCES purchases(id) ON DELETE RESTRICT,
    quantity SMALLINT NOT NULL CHECK (quantity > 0),
    movement_type VARCHAR(20) NOT NULL CHECK (movement_type IN ('EARN', 'REVERSAL', 'EXPIRATION', 'ADJUSTMENT', 'CARRY_IN', 'CARRY_OUT')),
    reverses_movement_id UUID REFERENCES stamp_movements(id),
    source_store_id UUID REFERENCES stores(id),
    created_by UUID REFERENCES profiles(id),
    reason TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE stamp_movements IS 'Registro inmutable de movimientos de sellos';

CREATE TABLE qr_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash TEXT NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    purpose VARCHAR(20) NOT NULL CHECK (purpose IN ('PURCHASE', 'REDEMPTION')),
    resource_id UUID, -- Referencia a customer_rewards si purpose = REDEMPTION
    store_id UUID REFERENCES stores(id),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by UUID REFERENCES profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE qr_tokens IS 'Tokens QR efímeros y dinámicos para compra o redención en caja';

-- =============================================================================
-- 6. RECOMPENSAS Y REDENCIONES
-- =============================================================================

CREATE TABLE reward_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    reward_type VARCHAR(30) NOT NULL CHECK (reward_type IN ('product', 'percentage_discount', 'fixed_discount', 'special')),
    reward_value TEXT,
    terms_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'inactive', 'archived')),
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE reward_store_eligibility (
    reward_definition_id UUID NOT NULL REFERENCES reward_definitions(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    PRIMARY KEY (reward_definition_id, store_id)
);

CREATE TABLE customer_rewards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    definition_id UUID NOT NULL REFERENCES reward_definitions(id) ON DELETE RESTRICT,
    card_id UUID NOT NULL REFERENCES loyalty_cards(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE', 'REDEEMED', 'EXPIRED', 'CANCELLED', 'PENDING_REVIEW')),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    redeemed_at TIMESTAMPTZ,
    terms_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE customer_rewards IS 'Instancias de beneficios emitidas al cliente tras completar 8 sellos';

CREATE TABLE reward_redemptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_reward_id UUID NOT NULL UNIQUE REFERENCES customer_rewards(id) ON DELETE RESTRICT,
    purchase_id UUID REFERENCES purchases(id) ON DELETE RESTRICT,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    cashier_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'reversed', 'under_review')),
    idempotency_key UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reversed_at TIMESTAMPTZ
);

COMMENT ON TABLE reward_redemptions IS 'Registro físico de canje de recompensa en caja';

-- =============================================================================
-- 7. PRODUCTOS DESTACADOS (VITRINA 5 ESPACIOS) Y BANNERS
-- =============================================================================

CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    price BIGINT CHECK (price >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'COP',
    image_path TEXT,
    featured_order SMALLINT CHECK (featured_order BETWEEN 1 AND 5),
    available BOOLEAN NOT NULL DEFAULT TRUE,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Garantizar unicidad de las posiciones 1 a 5 por negocio
CREATE UNIQUE INDEX idx_products_featured_order 
ON products(business_id, featured_order) 
WHERE featured_order IS NOT NULL;

CREATE TABLE banners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    title VARCHAR(120) NOT NULL,
    description TEXT,
    image_path TEXT NOT NULL,
    target_url TEXT,
    sort_order SMALLINT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 8. NOTIFICACIONES, DISPOSITIVOS Y HABEAS DATA (LEY 1581)
-- =============================================================================

CREATE TABLE push_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    platform VARCHAR(20) NOT NULL CHECK (platform IN ('android', 'ios', 'web')),
    push_token TEXT NOT NULL UNIQUE,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE notification_preferences (
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    category VARCHAR(30) NOT NULL CHECK (category IN ('transactional', 'marketing', 'proximity', 'reminders')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, category)
);

CREATE TABLE notification_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE RESTRICT,
    title VARCHAR(120) NOT NULL,
    body TEXT NOT NULL,
    category VARCHAR(30) NOT NULL DEFAULT 'marketing' CHECK (category IN ('transactional', 'marketing', 'proximity', 'automated')),
    audience_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    scheduled_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'scheduled', 'sending', 'sent', 'cancelled', 'failed')),
    created_by UUID REFERENCES profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE notification_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID REFERENCES notification_campaigns(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    device_id UUID REFERENCES push_devices(id) ON DELETE SET NULL,
    provider_message_id VARCHAR(120),
    status VARCHAR(20) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'delivered', 'failed', 'opened')),
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    purpose VARCHAR(50) NOT NULL CHECK (purpose IN ('general_terms', 'marketing_communications', 'geolocation_proximity')),
    policy_version VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'granted' CHECK (status IN ('granted', 'revoked')),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    evidence_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- =============================================================================
-- 9. AUDITORÍA FORENSE E IDEMPOTENCIA
-- =============================================================================

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID REFERENCES businesses(id) ON DELETE SET NULL,
    actor_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    store_id UUID REFERENCES stores(id) ON DELETE SET NULL,
    action VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    before_data JSONB,
    after_data JSONB,
    reason TEXT,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE idempotency_keys (
    key VARCHAR(128) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    endpoint VARCHAR(255) NOT NULL,
    request_hash TEXT NOT NULL,
    response_code INTEGER NOT NULL,
    response_body JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- =============================================================================
-- 10. ÍNDICES DE ALTO RENDIMIENTO (OPTIMIZACIÓN DE CONSULTAS)
-- =============================================================================

CREATE INDEX idx_stores_business ON stores(business_id);
CREATE INDEX idx_loyalty_cards_customer ON loyalty_cards(customer_id, status);
CREATE INDEX idx_stamp_movements_card ON stamp_movements(card_id);
CREATE INDEX idx_purchases_customer ON purchases(customer_id);
CREATE INDEX idx_purchases_store ON purchases(store_id, created_at);
CREATE INDEX idx_qr_tokens_hash ON qr_tokens(token_hash) WHERE consumed_at IS NULL;
CREATE INDEX idx_customer_rewards_customer ON customer_rewards(customer_id, status);
CREATE INDEX idx_audit_logs_business ON audit_logs(business_id, created_at DESC);
CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);

-- =============================================================================
-- FIN DEL ESQUEMA DDL
-- =============================================================================
