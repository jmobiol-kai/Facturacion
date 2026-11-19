from odoo import api, models


class AccountVatLedger(models.Model):
    _inherit = "account.vat.ledger"

    # (key, label, always shown). Keys are account.ar.vat.line fields present in every
    # l10n_ar_reports version, except "perc_iibb" and "otros", computed below.
    _KAI_PDF_COLUMNS = [
        ("base_21", "Neto 21%", True),
        ("base_10", "Neto 10,5%", False),
        ("base_27", "Neto 27%", False),
        ("base_5", "Neto 5%", False),
        ("base_25", "Neto 2,5%", False),
        ("not_taxed", "No grav./ Exento", True),
        ("vat_21", "IVA 21%", True),
        ("vat_10", "IVA 10,5%", False),
        ("vat_27", "IVA 27%", False),
        ("vat_5", "IVA 5%", False),
        ("vat_25", "IVA 2,5%", False),
        ("vat_per", "Perc. IVA", True),
        ("perc_iibb", "Perc. IIBB", True),
        ("otros", "Otros / Internos", True),
        ("total", "Total", True),
    ]
    _KAI_PDF_RATES = [
        ("21%", "base_21", "vat_21"),
        ("10,5%", "base_10", "vat_10"),
        ("27%", "base_27", "vat_27"),
        ("5%", "base_5", "vat_5"),
        ("2,5%", "base_25", "vat_25"),
    ]

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref("kai_l10n_ar_vat_ledger_pdf.action_report_vat_ledger_pdf").report_action(self)

    @api.model
    def _kai_pdf_fmt(self, value, blank_zero=True):
        value = value or 0.0
        if blank_zero and abs(value) < 0.005:
            return ""
        txt = "{:,.2f}".format(value)
        return txt.replace(",", "X").replace(".", ",").replace("X", ".")

    def _kai_pdf_other_tax_amounts(self, move_ids):
        """Per move: IIBB perceptions (tax group tribute 07) and other non-VAT taxes
        (no VAT code, tribute not 06/07, NULL tribute included). Same sign as account.ar.vat.line."""
        res = {}
        if not move_ids:
            return res
        self.env["account.move.line"].flush_model()
        self.env.cr.execute(
            """
            SELECT aml.move_id,
                   SUM(CASE WHEN tg.l10n_ar_tribute_afip_code = '07'
                            THEN aml.balance ELSE 0 END),
                   SUM(CASE WHEN tg.l10n_ar_vat_afip_code IS NULL
                             AND COALESCE(tg.l10n_ar_tribute_afip_code, '') NOT IN ('06', '07')
                            THEN aml.balance ELSE 0 END),
                   MIN(am.move_type)
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN account_tax t ON t.id = aml.tax_line_id
              JOIN account_tax_group tg ON tg.id = t.tax_group_id
             WHERE aml.move_id IN %s
             GROUP BY aml.move_id
            """,
            (tuple(move_ids),),
        )
        for move_id, iibb, otros, move_type in self.env.cr.fetchall():
            sign = -1.0 if move_type in ("out_invoice", "out_refund") else 1.0
            res[move_id] = (sign * float(iibb or 0.0), sign * float(otros or 0.0))
        return res

    def _get_vat_ledger_pdf_data(self):
        """Rows, totals and summary for the PDF. Lines are the same as the XLSX (invoice_ids)."""
        self.ensure_one()
        lines = self.invoice_ids.sorted(lambda l: (l.invoice_date or l.date, l.move_name or "", l.id))
        extra = self._kai_pdf_other_tax_amounts(lines.mapped("move_id").ids)
        keys = [c[0] for c in self._KAI_PDF_COLUMNS]
        data_rows = []
        totals = dict.fromkeys(keys, 0.0)
        for line in lines:
            iibb, otros = extra.get(line.move_id.id, (0.0, 0.0))
            vals = {}
            for key in keys:
                if key == "perc_iibb":
                    vals[key] = iibb
                elif key == "otros":
                    vals[key] = otros
                else:
                    vals[key] = line[key] or 0.0
                totals[key] += vals[key]
            data_rows.append((line, vals))
        columns = [(key, label) for key, label, always in self._KAI_PDF_COLUMNS
                   if always or abs(totals[key]) >= 0.005]
        rows = []
        for line, vals in data_rows:
            line_date = line.invoice_date or line.date
            rows.append({
                "date": line_date.strftime("%d/%m/%Y") if line_date else "",
                "doc_type": line.document_type_id.code or "",
                "number": line.move_name or "",
                "partner": (line.partner_name or "")[:38],
                "cuit": line.cuit or "",
                "values": [self._kai_pdf_fmt(vals[key]) for key, _label in columns],
            })
        fmt = self._kai_pdf_fmt
        summary = [
            {"rate": label, "base": fmt(totals[b], False), "vat": fmt(totals[v], False)}
            for label, b, v in self._KAI_PDF_RATES
            if abs(totals[b]) >= 0.005 or abs(totals[v]) >= 0.005
        ]
        company = self.company_id
        return {
            "title": "Libro IVA %s" % ("Compras" if self.type == "purchase" else "Ventas"),
            "partner_label": "Proveedor" if self.type == "purchase" else "Cliente",
            "company_name": company.name,
            "company_cuit": company.partner_id.vat or company.vat or "",
            "period": "%s al %s" % (self.date_from.strftime("%d/%m/%Y"), self.date_to.strftime("%d/%m/%Y")),
            "journals": ", ".join(self.journal_ids.mapped("name")),
            "columns": columns,
            "rows": rows,
            "count": len(rows),
            "totals": [fmt(totals[key], False) for key, _label in columns],
            "totals_raw": {key: round(totals[key], 2) for key in keys},
            "summary": summary,
            "summary_totals": {
                "base": fmt(sum(totals[r[1]] for r in self._KAI_PDF_RATES), False),
                "vat": fmt(sum(totals[r[2]] for r in self._KAI_PDF_RATES), False),
            },
            "others": [
                (label, fmt(totals[key], False))
                for key, label in (
                    ("not_taxed", "No gravado / Exento"),
                    ("vat_per", "Percepciones IVA"),
                    ("perc_iibb", "Percepciones IIBB"),
                    ("otros", "Otros / Internos"),
                    ("total", "Total"),
                )
            ],
        }
