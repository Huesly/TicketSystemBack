# =============================================================================
# SERVICIO DE FACTURACIÓN ELECTRÓNICA DIAN (ANEXO TÉCNICO 1.9 / UBL 2.1 COLOMBIA)
# Cálculo de CUFE SHA-384, generación de XML UBL 2.1, JSON DIAN y Reimpresión POS
# =============================================================================

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID
from xml.sax.saxutils import escape
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models.purchase import Purchase, DianSettings, ElectronicInvoice
from app.models.business import Business, Store
from app.models.user import Profile
from app.models.loyalty import StampMovement


class DianInvoiceService:
    @staticmethod
    async def get_or_create_settings(db: AsyncSession, business_id: Optional[UUID] = None) -> DianSettings:
        if not business_id:
            res = await db.execute(select(Business.id).limit(1))
            business_id = res.scalar_one_or_none()
            if not business_id:
                raise HTTPException(status_code=500, detail="Empresa no configurada en el sistema")

        stmt = select(DianSettings).where(DianSettings.business_id == business_id)
        settings = (await db.execute(stmt)).scalar_one_or_none()
        if not settings:
            settings = DianSettings(
                id=uuid.uuid4(),
                business_id=business_id
            )
            db.add(settings)
            await db.commit()
            await db.refresh(settings)
        return settings

    @staticmethod
    def compute_cufe_sha384(
        num_fac: str,
        fec_fac: str,
        hor_fac: str,
        val_sub: int,
        val_inc: int,
        val_tot: int,
        nit_ofe: str,
        num_adq: str,
        cl_tec: str,
        ambiente: str = "2"
    ) -> str:
        """
        Fórmula oficial Anexo Técnico 1.9 DIAN (SHA-384):
        NumFac + FecFac + HorFac + ValFac + CodImp1('01') + ValImp1('0.00') +
        CodImp2('04') + ValImp2(INC) + CodImp3('03') + ValImp3('0.00') +
        ValTot + NitOFE + NumAdq + ClTec + TipoAmbiente
        """
        raw_chain = (
            f"{num_fac}"
            f"{fec_fac}"
            f"{hor_fac}"
            f"{val_sub:.2f}"
            f"010.00"
            f"04{val_inc:.2f}"
            f"030.00"
            f"{val_tot:.2f}"
            f"{nit_ofe}"
            f"{num_adq}"
            f"{cl_tec}"
            f"{ambiente}"
        )
        return hashlib.sha384(raw_chain.encode("utf-8")).hexdigest()

    @classmethod
    async def ensure_invoice_for_purchase(
        cls,
        db: AsyncSession,
        purchase_id: UUID,
        payment_method: str = "Efectivo",
        customer_doc_type: Optional[str] = None,
        customer_doc_number: Optional[str] = None,
        item_description: Optional[str] = None,
        increment_reprint: bool = False
    ) -> Dict[str, Any]:
        """
        Obtiene o construye la Factura Electrónica DIAN para una compra registrada.
        Si increment_reprint=True, incrementa el contador de reimpresiones auditadas.
        """
        purchase = await db.get(Purchase, purchase_id)
        if not purchase:
            raise HTTPException(status_code=404, detail="Factura o compra no encontrada")

        store = await db.get(Store, purchase.store_id)
        customer = await db.get(Profile, purchase.customer_id)
        cashier = await db.get(Profile, purchase.cashier_id)
        settings = await cls.get_or_create_settings(db, purchase.business_id)

        inv_stmt = select(ElectronicInvoice).where(ElectronicInvoice.purchase_id == purchase.id)
        invoice = (await db.execute(inv_stmt)).scalar_one_or_none()

        if not invoice:
            # Calcular sellos acreditados en esta compra
            stamps_stmt = (
                select(func.coalesce(func.sum(StampMovement.quantity), 0))
                .where(
                    StampMovement.purchase_id == purchase.id,
                    StampMovement.movement_type.in_(["EARN", "CARRY_IN"])
                )
            )
            stamps_awarded = (await db.execute(stamps_stmt)).scalar_one()

            # Incrementar consecutivo DIAN
            settings.consecutivo_actual = (settings.consecutivo_actual or 1000) + 1
            consecutive = settings.consecutivo_actual
            prefix = settings.prefijo or "SETT"
            invoice_number = f"{prefix}{consecutive}"

            # Desglose tributario Colombia: Impoconsumo 8% incluido en el valor final
            total_cop = int(purchase.eligible_amount)
            discount_cop = int(purchase.discount_amount or 0)
            rate = settings.tarifa_impoconsumo_pct if settings.tarifa_impoconsumo_pct is not None else 8
            if rate > 0:
                subtotal_cop = int(round(total_cop / (1 + rate / 100.0)))
                tax_amount_cop = max(0, total_cop - subtotal_cop)
            else:
                subtotal_cop = total_cop
                tax_amount_cop = 0

            issue_dt = purchase.created_at or datetime.now(timezone.utc)
            fec_fac = issue_dt.strftime("%Y-%m-%d")
            hor_fac = issue_dt.strftime("%H:%M:%S-05:00")

            doc_type = (customer_doc_type or (customer.document_type if customer else None) or "CC").upper()
            doc_num = (customer_doc_number or (customer.document_number if customer else None) or "222222222222").strip()

            # Actualizar documento en el perfil del cliente si lo proporcionó en caja
            if customer and customer_doc_number and customer_doc_number.strip() != "222222222222":
                customer.document_type = doc_type
                customer.document_number = doc_num

            cufe = cls.compute_cufe_sha384(
                num_fac=invoice_number,
                fec_fac=fec_fac,
                hor_fac=hor_fac,
                val_sub=subtotal_cop,
                val_inc=tax_amount_cop,
                val_tot=total_cop,
                nit_ofe=settings.nit_emisor,
                num_adq=doc_num,
                cl_tec=settings.clave_tecnica,
                ambiente=settings.ambiente
            )
            qr_url = f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}"

            items_list = [
                {
                    "code": "CAFE-ESP-01",
                    "description": item_description or f"Consumo Cafetería de Especialidad ({ purchase.receipt_reference })",
                    "quantity": 1,
                    "unit_price_cop": subtotal_cop,
                    "tax_rate_pct": rate,
                    "tax_amount_cop": tax_amount_cop,
                    "line_total_cop": total_cop
                }
            ]

            invoice = ElectronicInvoice(
                id=uuid.uuid4(),
                purchase_id=purchase.id,
                business_id=purchase.business_id,
                store_id=purchase.store_id,
                customer_id=purchase.customer_id,
                invoice_number=invoice_number,
                prefix=prefix,
                consecutive=consecutive,
                cufe=cufe,
                qr_url=qr_url,
                issue_date=issue_dt,
                payment_method=payment_method or "Efectivo",
                customer_doc_type=doc_type,
                customer_doc_number=doc_num,
                customer_name=customer.full_name if customer else "Consumidor Final",
                customer_email=customer.email if customer else "consumidor@confytickets.co",
                subtotal_cop=subtotal_cop,
                tax_type="INC",
                tax_rate_pct=rate,
                tax_amount_cop=tax_amount_cop,
                discount_cop=discount_cop,
                total_cop=total_cop,
                stamps_awarded=stamps_awarded,
                items_json=items_list,
                dian_status="CREDIT_NOTE_ISSUED" if purchase.status == "cancelled" else "READY_FOR_DIAN",
                reprint_count=0
            )
            db.add(invoice)
            await db.commit()
            await db.refresh(invoice)

        if increment_reprint:
            invoice.reprint_count = (invoice.reprint_count or 0) + 1
            invoice.last_reprinted_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(invoice)

        return cls.serialize_invoice(invoice, purchase, settings, store, cashier)

    @classmethod
    def serialize_invoice(
        cls,
        invoice: ElectronicInvoice,
        purchase: Purchase,
        settings: DianSettings,
        store: Optional[Store],
        cashier: Optional[Profile]
    ) -> Dict[str, Any]:
        return {
            "invoice_id": str(invoice.id),
            "purchase_id": str(purchase.id),
            "receipt_reference": purchase.receipt_reference,
            "invoice_number": invoice.invoice_number,
            "prefix": invoice.prefix,
            "consecutive": invoice.consecutive,
            "cufe": invoice.cufe,
            "qr_url": invoice.qr_url,
            "issue_date": invoice.issue_date.isoformat(),
            "status": purchase.status,
            "dian_status": invoice.dian_status,
            "payment_method": invoice.payment_method,
            "reprint_count": invoice.reprint_count,
            "last_reprinted_at": invoice.last_reprinted_at.isoformat() if invoice.last_reprinted_at else None,
            "credit_note_number": invoice.credit_note_number,
            "credit_note_cude": invoice.credit_note_cude,
            "cancellation_reason": invoice.cancellation_reason,
            "emitter": {
                "nit": settings.nit_emisor,
                "dv": settings.dv_emisor,
                "razon_social": settings.razon_social,
                "nombre_comercial": settings.nombre_comercial,
                "regimen_fiscal": settings.regimen_fiscal,
                "actividad_ciiu": settings.actividad_ciiu,
                "resolucion_numero": settings.resolucion_numero,
                "resolucion_fecha": settings.resolucion_fecha,
                "resolucion_vigencia_meses": settings.resolucion_vigencia_meses,
                "prefijo": settings.prefijo,
                "rango_desde": settings.rango_desde,
                "rango_hasta": settings.rango_hasta,
                "ambiente": "Producción" if settings.ambiente == "1" else "Habilitación / Pruebas DIAN",
                "direccion": store.address if store else settings.direccion_emisor,
                "municipio": store.city if store else settings.municipio_emisor,
                "departamento": store.department if store else settings.departamento_emisor,
                "telefono": (store.phone if store and store.phone else settings.telefono_emisor),
                "correo": settings.correo_emisor,
                "store_name": store.name if store else "Sede Principal",
                "store_code": store.code if store else "SEDE-01"
            },
            "customer": {
                "id": str(invoice.customer_id),
                "full_name": invoice.customer_name,
                "email": invoice.customer_email,
                "document_type": invoice.customer_doc_type,
                "document_number": invoice.customer_doc_number
            },
            "cashier_name": cashier.full_name if cashier else "Cajero POS",
            "totals": {
                "gross_amount_cop": int(purchase.gross_amount),
                "discount_cop": int(invoice.discount_cop),
                "subtotal_base_cop": int(invoice.subtotal_cop),
                "tax_type": invoice.tax_type,
                "tax_rate_pct": invoice.tax_rate_pct,
                "tax_amount_cop": int(invoice.tax_amount_cop),
                "total_payable_cop": int(invoice.total_cop),
                "currency": purchase.currency or "COP",
                "stamps_awarded": invoice.stamps_awarded
            },
            "items": invoice.items_json or []
        }

    @classmethod
    def generate_ubl21_xml(cls, data: Dict[str, Any]) -> str:
        """
        Genera el archivo XML estándar UBL 2.1 (Anexo Técnico 1.9 DIAN Colombia)
        listo para validación o carga en plataforma de facturación electrónica.
        """
        em = data["emitter"]
        cu = data["customer"]
        tot = data["totals"]
        issue_dt = datetime.fromisoformat(data["issue_date"])
        fec_str = issue_dt.strftime("%Y-%m-%d")
        hor_str = issue_dt.strftime("%H:%M:%S-05:00")

        lines_xml = ""
        for idx, item in enumerate(data["items"], start=1):
            lines_xml += f"""
    <cac:InvoiceLine>
        <cbc:ID>{idx}</cbc:ID>
        <cbc:InvoicedQuantity unitCode="94">{item.get("quantity", 1)}</cbc:InvoicedQuantity>
        <cbc:LineExtensionAmount currencyID="COP">{item.get("unit_price_cop", tot["subtotal_base_cop"]):.2f}</cbc:LineExtensionAmount>
        <cac:TaxTotal>
            <cbc:TaxAmount currencyID="COP">{item.get("tax_amount_cop", tot["tax_amount_cop"]):.2f}</cbc:TaxAmount>
            <cac:TaxSubtotal>
                <cbc:TaxableAmount currencyID="COP">{item.get("unit_price_cop", tot["subtotal_base_cop"]):.2f}</cbc:TaxableAmount>
                <cbc:TaxAmount currencyID="COP">{item.get("tax_amount_cop", tot["tax_amount_cop"]):.2f}</cbc:TaxAmount>
                <cac:TaxCategory>
                    <cbc:Percent>{tot["tax_rate_pct"]:.2f}</cbc:Percent>
                    <cac:TaxScheme>
                        <cbc:ID>04</cbc:ID>
                        <cbc:Name>INC</cbc:Name>
                    </cac:TaxScheme>
                </cac:TaxCategory>
            </cac:TaxSubtotal>
        </cac:TaxTotal>
        <cac:Item>
            <cbc:Description>{escape(str(item.get("description", "Consumo Cafetería")))}</cbc:Description>
            <cac:StandardItemIdentification>
                <cbc:ID schemeID="999">{escape(str(item.get("code", "CAFE-01")))}</cbc:ID>
            </cac:StandardItemIdentification>
        </cac:Item>
        <cac:Price>
            <cbc:PriceAmount currencyID="COP">{item.get("unit_price_cop", tot["subtotal_base_cop"]):.2f}</cbc:PriceAmount>
        </cac:Price>
    </cac:InvoiceLine>"""

        xml_doc = f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
         xmlns:sts="dian:gov:co:facturaelectronica:Structures-2-1">
    <ext:UBLExtensions>
        <ext:UBLExtension>
            <ext:ExtensionContent>
                <sts:DianExtensions>
                    <sts:InvoiceControl>
                        <sts:InvoiceAuthorization>{escape(str(em["resolucion_numero"]))}</sts:InvoiceAuthorization>
                        <sts:AuthorizationPeriod>
                            <cbc:StartDate>{escape(str(em["resolucion_fecha"]))}</cbc:StartDate>
                        </sts:AuthorizationPeriod>
                        <sts:AuthorizedInvoices>
                            <sts:Prefix>{escape(str(em["prefijo"]))}</sts:Prefix>
                            <sts:From>{em["rango_desde"]}</sts:From>
                            <sts:To>{em["rango_hasta"]}</sts:To>
                        </sts:AuthorizedInvoices>
                    </sts:InvoiceControl>
                    <sts:InvoiceSource>
                        <cbc:IdentificationCode listAgencyID="6" listAgencyName="United Nations Economic Commission for Europe" listSchemeURI="urn:oasis:names:specification:ubl:codelist:gc:CountryIdentificationCode-2.1">CO</cbc:IdentificationCode>
                    </sts:InvoiceSource>
                    <sts:QRCode>{escape(str(data["qr_url"]))}</sts:QRCode>
                </sts:DianExtensions>
            </ext:ExtensionContent>
        </ext:UBLExtension>
    </ext:UBLExtensions>
    <cbc:UBLVersionID>UBL 2.1</cbc:UBLVersionID>
    <cbc:CustomizationID>10</cbc:CustomizationID>
    <cbc:ProfileID>DIAN 2.1: Factura Electrónica de Venta</cbc:ProfileID>
    <cbc:ID>{escape(str(data["invoice_number"]))}</cbc:ID>
    <cbc:UUID schemeID="2" schemeName="CUFE-SHA384">{data["cufe"]}</cbc:UUID>
    <cbc:IssueDate>{fec_str}</cbc:IssueDate>
    <cbc:IssueTime>{hor_str}</cbc:IssueTime>
    <cbc:InvoiceTypeCode>01</cbc:InvoiceTypeCode>
    <cbc:Note>Comprobante POS Internamente Vinculado: {escape(str(data["receipt_reference"]))} | Sellos Acreditados: {tot["stamps_awarded"]}</cbc:Note>
    <cbc:DocumentCurrencyCode>COP</cbc:DocumentCurrencyCode>
    <cac:AccountingSupplierParty>
        <cbc:AdditionalAccountID>1</cbc:AdditionalAccountID>
        <cac:Party>
            <cac:PartyName>
                <cbc:Name>{escape(str(em["nombre_comercial"]))}</cbc:Name>
            </cac:PartyName>
            <cac:PartyTaxScheme>
                <cbc:RegistrationName>{escape(str(em["razon_social"]))}</cbc:RegistrationName>
                <cbc:CompanyID schemeID="{escape(str(em["dv"]))}" schemeName="31">{escape(str(em["nit"]))}</cbc:CompanyID>
                <cac:TaxScheme>
                    <cbc:ID>ZZ</cbc:ID>
                    <cbc:Name>{escape(str(em["regimen_fiscal"]))}</cbc:Name>
                </cac:TaxScheme>
            </cac:PartyTaxScheme>
            <cac:Contact>
                <cbc:Telephone>{escape(str(em["telefono"]))}</cbc:Telephone>
                <cbc:ElectronicMail>{escape(str(em["correo"]))}</cbc:ElectronicMail>
            </cac:Contact>
        </cac:Party>
    </cac:AccountingSupplierParty>
    <cac:AccountingCustomerParty>
        <cbc:AdditionalAccountID>2</cbc:AdditionalAccountID>
        <cac:Party>
            <cac:PartyTaxScheme>
                <cbc:RegistrationName>{escape(str(cu["full_name"]))}</cbc:RegistrationName>
                <cbc:CompanyID schemeName="{escape(str(cu["document_type"]))}">{escape(str(cu["document_number"]))}</cbc:CompanyID>
                <cac:TaxScheme>
                    <cbc:ID>ZZ</cbc:ID>
                    <cbc:Name>No aplica</cbc:Name>
                </cac:TaxScheme>
            </cac:PartyTaxScheme>
            <cac:Contact>
                <cbc:ElectronicMail>{escape(str(cu["email"]))}</cbc:ElectronicMail>
            </cac:Contact>
        </cac:Party>
    </cac:AccountingCustomerParty>
    <cac:PaymentMeans>
        <cbc:ID>1</cbc:ID>
        <cbc:PaymentMeansCode>10</cbc:PaymentMeansCode>
        <cbc:InstructionNote>{escape(str(data["payment_method"]))}</cbc:InstructionNote>
    </cac:PaymentMeans>
    <cac:TaxTotal>
        <cbc:TaxAmount currencyID="COP">{tot["tax_amount_cop"]:.2f}</cbc:TaxAmount>
        <cac:TaxSubtotal>
            <cbc:TaxableAmount currencyID="COP">{tot["subtotal_base_cop"]:.2f}</cbc:TaxableAmount>
            <cbc:TaxAmount currencyID="COP">{tot["tax_amount_cop"]:.2f}</cbc:TaxAmount>
            <cac:TaxCategory>
                <cbc:Percent>{tot["tax_rate_pct"]:.2f}</cbc:Percent>
                <cac:TaxScheme>
                    <cbc:ID>04</cbc:ID>
                    <cbc:Name>INC</cbc:Name>
                </cac:TaxScheme>
            </cac:TaxCategory>
        </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:LegalMonetaryTotal>
        <cbc:LineExtensionAmount currencyID="COP">{tot["subtotal_base_cop"]:.2f}</cbc:LineExtensionAmount>
        <cbc:TaxExclusiveAmount currencyID="COP">{tot["subtotal_base_cop"]:.2f}</cbc:TaxExclusiveAmount>
        <cbc:TaxInclusiveAmount currencyID="COP">{tot["total_payable_cop"]:.2f}</cbc:TaxInclusiveAmount>
        <cbc:AllowanceTotalAmount currencyID="COP">{tot["discount_cop"]:.2f}</cbc:AllowanceTotalAmount>
        <cbc:PayableAmount currencyID="COP">{tot["total_payable_cop"]:.2f}</cbc:PayableAmount>
    </cac:LegalMonetaryTotal>{lines_xml}
</Invoice>
"""
        return xml_doc
