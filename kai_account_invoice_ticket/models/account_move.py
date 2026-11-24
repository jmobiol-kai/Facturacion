import base64
from io import BytesIO
from itertools import groupby

from odoo import models
from odoo.tools import float_is_zero
from odoo.tools.misc import formatLang, format_date


class AccountMove(models.Model):
    _inherit = "account.move"

    def _kai_ticket_values(self):
        """Datos del ticket, en el orden del recibo del Punto de Venta."""
        self.ensure_one()
        title, code, has_letter = self._kai_ticket_title()
        partner = self.partner_id
        branch = self._kai_ticket_branch()
        taxes = self._kai_ticket_tax_block()
        afip = self._kai_ticket_afip()
        customer_id_label, customer_id_number = self._kai_ticket_customer_id(partner)
        origin = (self.invoice_origin or "").strip()
        reference = (self.ref or "").strip()
        cashier = (self.invoice_user_id.name or "").split(" ")[0]
        return {
            "title": title,
            "code": code,
            "logo_style": self._kai_ticket_logo_style(),
            "show_due": bool(
                self.invoice_date_due and self.invoice_date_due != self.invoice_date
            ),
            "origin": origin or False,
            "show_ref": bool(reference) and reference != origin,
            "ref": reference or False,
            "has_letter": has_letter,
            "issuer_vat_cond": branch["responsibility"] if has_letter else False,
            "iibb": branch["iibb"] if has_letter else False,
            "start_date": branch["start_date"] if has_letter else False,
            "cashier": cashier or False,
            "customer_name": self._kai_ticket_customer_name(partner),
            "customer_address": self._kai_ticket_partner_address(partner),
            "customer_vat_cond": partner.l10n_ar_afip_responsibility_type_id.name or False,
            "customer_id_label": customer_id_label,
            "customer_id_number": customer_id_number,
            "lines": self._kai_ticket_orderlines(),
            "tax_subtotals": taxes["subtotals"],
            "same_tax_base": taxes["same_tax_base"],
            "rounding": taxes["rounding"],
            "total": taxes["total"],
            "discount_total": self._kai_ticket_total_discount(),
            "payments": self._kai_ticket_payments(),
            "fiscal_summary": self._kai_ticket_fiscal_summary(),
            "afip_cae": afip["cae"],
            "afip_cae_due": afip["cae_due"],
            "afip_qr": afip["qr"],
            "branch_name": branch["name"],
            "branch_address": branch["address"],
            "cuit_label": branch["cuit_label"],
            "cuit_number": branch["cuit_number"],
        }

    def _kai_ticket_title(self):
        self.ensure_one()
        document = self.l10n_latam_document_type_id
        number = self.l10n_latam_document_number or self.name or ""
        if document:
            parts = [document.report_name or "", document.l10n_ar_letter or "", number]
            code = document.code or False
            if code and str(code).isdigit():
                code = "%02d" % int(code)
            title = " ".join(part for part in parts if part)
            return title, code or False, bool(document.l10n_ar_letter)
        labels = {
            "out_refund": "Nota de crédito",
            "out_receipt": "Recibo",
        }
        title = " ".join(part for part in (labels.get(self.move_type, "Factura"), number) if part)
        return title, False, False

    def _kai_ticket_logo_style(self):
        """Encaja el logo en 50 mm × 20 mm. wkhtmltopdf ignora max-height y recorta."""
        logo = self.company_id.logo
        fallback = "width:auto;max-width:50mm;max-height:20mm;height:auto;"
        if not logo:
            return fallback
        try:
            from PIL import Image
            image = Image.open(BytesIO(base64.b64decode(logo)))
            width, height = image.size
        except Exception:
            return fallback
        if not width or not height:
            return fallback
        max_w, max_h = 50.0, 20.0
        aspect = width / float(height)
        if aspect >= (max_w / max_h):
            display_w = max_w
            display_h = max_w / aspect
        else:
            display_h = max_h
            display_w = max_h * aspect
        return "width:%.2fmm;height:%.2fmm;" % (display_w, display_h)

    def _kai_ticket_customer_name(self, partner):
        if not partner:
            return False
        if partner.parent_id and partner.parent_id.name and partner.parent_id.name != partner.name:
            return "%s, %s" % (partner.parent_id.name, partner.name or "")
        return partner.name or False

    def _kai_ticket_customer_id(self, partner):
        """Tipo y número solo si hay identificación y no es el código AFIP 99."""
        id_type = partner.l10n_latam_identification_type_id
        if not partner.vat or not id_type or id_type.l10n_ar_afip_code == "99":
            return False, False
        number = partner.l10n_ar_vat or self._kai_ticket_compact_vat(partner.vat)
        return id_type.name or "CUIT", number or partner.vat

    def _kai_ticket_partner_address(self, partner):
        if not partner:
            return False
        street = ", ".join(part for part in (partner.street, partner.street2) if part)
        locality = ", ".join(
            part for part in (
                partner.city,
                partner.state_id.name,
                partner.zip,
            ) if part
        )
        return ", ".join(part for part in (street, locality) if part) or False

    def _kai_ticket_compact_vat(self, vat):
        digits = "".join(char for char in (vat or "") if char.isdigit())
        return digits or (vat or "").replace("-", "").replace(" ", "")

    def _kai_ticket_branch(self):
        """Sucursal del pie: dirección del punto de venta del diario, o el diario."""
        self.ensure_one()
        company = self.company_id
        emitter = self.journal_id.l10n_ar_afip_pos_partner_id
        if emitter:
            name = emitter.name or self.journal_id.name or company.name
            lines = self._kai_ticket_address_lines(emitter)
        else:
            name = self.journal_id.name or company.name
            lines = self._kai_ticket_address_lines(company.partner_id)
        company_partner = company.partner_id
        vat = company_partner.l10n_ar_vat or self._kai_ticket_compact_vat(company_partner.vat)
        iibb = False
        if company.l10n_ar_gross_income_type == "exempt":
            iibb = "Exento"
        elif company.l10n_ar_gross_income_number:
            iibb = company.l10n_ar_gross_income_number
        address = ", ".join(lines)
        return {
            "name": name or "",
            "address": address or False,
            "responsibility": company.l10n_ar_afip_responsibility_type_id.name or False,
            "iibb": iibb,
            "start_date": format_date(self.env, company.l10n_ar_afip_start_date)
            if company.l10n_ar_afip_start_date else False,
            "cuit_label": company.country_id.vat_label or "CUIT",
            "cuit_number": vat or False,
        }

    def _kai_ticket_address_lines(self, partner):
        lines = []
        street = ", ".join(part for part in (partner.street, partner.street2) if part)
        if street:
            lines.append(street)
        locality = ", ".join(
            part for part in (
                partner.city,
                partner.state_id.code or partner.state_id.name,
                partner.zip,
            ) if part
        )
        if locality:
            lines.append(locality)
        return lines

    def _kai_ticket_orderlines(self):
        self.ensure_one()
        rows = []
        for line in self._get_move_lines_to_report():
            if line.display_type == "line_note":
                rows.append({"kind": "note", "name": line.name or ""})
            elif line.display_type in ("line_section", "line_subsection"):
                if line.collapse_composition or line.collapse_prices:
                    rows.extend(self._kai_ticket_collapsed_rows(line))
                else:
                    rows.append({"kind": "section", "name": line.name or ""})
            elif line.display_type == "product":
                rows.append(self._kai_ticket_product_row(line))
        return rows

    def _kai_ticket_collapsed_rows(self, section):
        products = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and section in (line.parent_id, line.parent_id.parent_id)
        )
        if not products:
            return [{"kind": "section", "name": section.name or ""}]
        if not section.collapse_composition:
            return [self._kai_ticket_summary_row(section, products)]
        rows = []
        ordered = products.sorted(key=lambda line: tuple(line.tax_ids.ids))
        for _tax_key, grouped in groupby(ordered, key=lambda line: tuple(line.tax_ids.ids)):
            bucket = self.env["account.move.line"]
            for line in grouped:
                bucket |= line
            rows.append(self._kai_ticket_summary_row(section, bucket))
        return rows

    def _kai_ticket_summary_row(self, section, lines):
        amount = sum(line._l10n_ar_prices_and_taxes()["price_subtotal"] for line in lines)
        return {
            "kind": "product",
            "qty": "1",
            "name": (section.name or "").split("\n", 1)[0].strip(),
            "price": self.currency_id.round(amount),
            "show_unit": False,
            "price_unit": 0.0,
            "uom": "",
            "discount": 0.0,
            "discount_label": False,
        }

    def _kai_ticket_product_row(self, line):
        prices = line._l10n_ar_prices_and_taxes()
        discount = line.discount or 0.0
        unit = prices["price_unit"]
        return {
            "kind": "product",
            "qty": self._kai_ticket_format_qty(line.quantity),
            "name": self._kai_ticket_product_name(line),
            "price": self.currency_id.round(prices["price_subtotal"]),
            "show_unit": not float_is_zero(unit, precision_digits=2),
            "price_unit": unit,
            "uom": line.product_uom_id.name or "",
            "discount": discount,
            "discount_label": self._kai_ticket_format_qty(discount) if discount else False,
        }

    def _kai_ticket_product_name(self, line):
        """Nombre del producto, sin el código interno entre corchetes."""
        custom = (line.name or "").split("\n", 1)[0].strip()
        if custom.startswith("[") and "]" in custom:
            custom = custom[custom.find("]") + 1:].strip()
        if line.product_id:
            product_name = line.product_id.with_context(lang=self.partner_id.lang).name or ""
            display = (line.product_id.display_name or "").strip()
            plain = display
            if display.startswith("[") and "]" in display:
                plain = display[display.find("]") + 1:].strip()
            if custom and custom not in {product_name, display, plain}:
                return custom
            return product_name or custom
        return custom

    def _kai_ticket_format_qty(self, quantity):
        """Un solo token: 1, 2 o 0,19. Sin ceros de más ni parte decimal separada."""
        nearest = round(quantity)
        if float_is_zero(quantity - nearest, precision_digits=4):
            return str(int(nearest))
        lang = self.env["res.lang"].search([("code", "=", self.env.lang)], limit=1)
        point = (lang.decimal_point if lang else ",") or ","
        formatted = formatLang(self.env, quantity, dp="Product Unit")
        if point in formatted:
            whole, frac = formatted.rsplit(point, 1)
            frac = frac.rstrip("0")
            if not frac:
                return whole
            return "%s%s%s" % (whole, point, frac)
        return formatted

    def _kai_ticket_fiscal_label(self, name):
        """El nombre de l10n_ar ya trae el símbolo. El importe lo pone el widget."""
        label = (name or "").strip()
        currency = self.currency_id
        for token in (currency.symbol, currency.name):
            token = (token or "").strip()
            if token and label.endswith(token):
                label = label[:-len(token)].strip(" :")
        return label

    def _kai_ticket_total_discount(self):
        self.ensure_one()
        total = 0.0
        for line in self.invoice_line_ids.filtered(lambda item: item.display_type == "product" and item.discount):
            prices = line._l10n_ar_prices_and_taxes()
            total += prices["price_unit"] * line.quantity - prices["price_subtotal"]
        rounded = self.currency_id.round(total)
        return rounded if not float_is_zero(rounded, precision_rounding=self.currency_id.rounding) else 0.0

    def _kai_ticket_tax_block(self):
        self.ensure_one()
        if not self.tax_totals:
            return {
                "subtotals": [],
                "same_tax_base": True,
                "rounding": None,
                "total": self.amount_total,
            }
        totals = self._l10n_ar_get_invoice_totals_for_report() or {}
        subtotals = []
        for subtotal in totals.get("subtotals") or []:
            groups = subtotal.get("tax_groups") or []
            if not groups:
                continue
            subtotals.append({
                "base": subtotal.get("base_amount_currency"),
                "groups": [{
                    "name": group.get("group_name"),
                    "label": group.get("group_label") or False,
                    "amount": group.get("tax_amount_currency"),
                    "base": group.get("base_amount_currency"),
                } for group in groups],
            })
        rounding = totals.get("cash_rounding_base_amount_currency") if (
            "cash_rounding_base_amount_currency" in totals
        ) else None
        return {
            "subtotals": subtotals,
            "same_tax_base": totals.get("same_tax_base", True),
            "rounding": rounding,
            "total": totals.get("total_amount_currency", self.amount_total),
        }

    def _kai_ticket_payments(self):
        self.ensure_one()
        widget = self.sudo().invoice_payments_widget or {}
        payments = []
        for item in widget.get("content") or []:
            if item.get("is_exchange"):
                continue
            payments.append({
                "name": item.get("payment_method_name") or item.get("journal_name") or item.get("ref") or "",
                "amount": item.get("amount") or 0.0,
            })
        return payments

    def _kai_ticket_fiscal_summary(self):
        """Régimen de transparencia fiscal (RG 5614), el mismo detalle que el A4."""
        self.ensure_one()
        if not hasattr(self, "_l10n_ar_get_invoice_custom_tax_summary_for_report"):
            return []
        rows = []
        for detail in self._l10n_ar_get_invoice_custom_tax_summary_for_report() or []:
            rows.append({
                "name": self._kai_ticket_fiscal_label(detail.get("name")),
                "amount": detail.get("tax_amount_currency"),
            })
        return rows

    def _kai_ticket_afip(self):
        """CAE y QR solo si otro módulo agregó los campos y tienen valor."""
        self.ensure_one()

        def value(name):
            if name not in self._fields:
                return False
            return self[name] or False

        return {
            "cae": value("afip_auth_code"),
            "cae_due": value("afip_auth_code_due"),
            "qr": value("afip_qr_code"),
        }
