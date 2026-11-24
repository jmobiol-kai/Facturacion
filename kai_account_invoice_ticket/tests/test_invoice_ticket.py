from ast import literal_eval

from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestInvoiceTicket(AccountTestInvoicingCommon):

    def _customer_invoice(self, post=True):
        invoice = self._create_invoice(
            move_type="out_invoice",
            partner_id=self.partner_a.id,
            invoice_date=fields.Date.today(),
            invoice_line_ids=[
                Command.create({
                    "display_type": "line_section",
                    "name": "Sección ticket",
                }),
                self._prepare_invoice_line(
                    price_unit=3800.0,
                    quantity=1.0,
                    name="[ABC] Agua x 1.5 Lts",
                    tax_ids=[Command.clear()],
                ),
                Command.create({
                    "display_type": "line_note",
                    "name": "Nota ticket",
                }),
            ],
            post=False,
        )
        journal = invoice.journal_id
        if "l10n_latam_use_documents" in journal._fields:
            journal.l10n_latam_use_documents = False
        if "l10n_ar_afip_ws" in journal._fields:
            journal.l10n_ar_afip_ws = False
        product_line = invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        product_line.name = "[ABC] Agua x 1.5 Lts"
        invoice.invoice_user_id = self.env.user
        if post:
            invoice.action_post()
        return invoice

    def test_report_action_is_ticket_not_a4(self):
        report = self.env.ref("kai_account_invoice_ticket.action_report_invoice_ticket")
        paper = report.paperformat_id
        self.assertEqual(report.name, "Imprimir ticket factura")
        self.assertEqual(report.report_type, "qweb-pdf")
        self.assertEqual(report.model, "account.move")
        self.assertEqual(report.binding_model_id.model, "account.move")
        self.assertEqual(report.binding_type, "report")
        self.assertFalse(report.is_invoice_report)
        self.assertIn("_ticket", report.print_report_name)
        domain = literal_eval(report.domain)
        self.assertIn(("move_type", "in", ("out_invoice", "out_refund", "out_receipt")), domain)
        self.assertEqual(paper.format, "custom")
        self.assertEqual(paper.page_width, 80)
        self.assertEqual(paper.page_height, 297)
        self.assertEqual(paper.orientation, "Portrait")
        self.assertEqual(paper.dpi, 90)
        self.assertFalse(paper.header_line)
        self.assertEqual(paper.header_spacing, 0)
        self.assertTrue(paper.disable_shrinking)
        for margin in (paper.margin_top, paper.margin_bottom, paper.margin_left, paper.margin_right):
            self.assertAlmostEqual(margin, 3.0)

    def test_header_button_prints_ticket_without_marking_sent(self):
        invoice = self._customer_invoice()
        self.assertFalse(invoice.is_move_sent)
        action = invoice.action_print_invoice_ticket()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(
            action["report_name"],
            "kai_account_invoice_ticket.report_invoice_ticket",
        )
        self.assertFalse(invoice.is_move_sent)

        arch = self.env["account.move"].get_view(view_type="form")["arch"]
        self.assertIn("action_print_pdf", arch)
        self.assertIn("action_print_invoice_ticket", arch)
        self.assertLess(
            arch.rindex('name="action_print_pdf"'),
            arch.index('name="action_print_invoice_ticket"'),
        )
        self.assertIn("Ticket Factura", arch)
        self.assertIn("out_invoice", arch)
        self.assertIn("draft", arch)
        self.assertIn("posted", arch)

        report = self.env.ref("kai_account_invoice_ticket.action_report_invoice_ticket")
        self.assertEqual(report.binding_type, "report")

    def test_compact_cuit_has_no_dashes(self):
        move = self.env["account.move"]
        self.assertEqual(move._kai_ticket_compact_vat("30-71542606-0"), "30715426060")

    def test_render_posted_customer_invoice(self):
        invoice = self._customer_invoice()
        values = invoice._kai_ticket_values()
        document = invoice.l10n_latam_document_type_id
        if document:
            self.assertIn(document.report_name, values["title"])
            if document.l10n_ar_letter:
                self.assertIn(document.l10n_ar_letter, values["title"])
        else:
            self.assertTrue(values["title"].startswith("Factura"))
        number = invoice.l10n_latam_document_number or invoice.name
        self.assertIn(number, values["title"])
        self.assertEqual(
            values["cashier"],
            (invoice.invoice_user_id.name or "").split(" ")[0],
        )
        self.assertEqual(values["branch_name"], invoice.journal_id.name)
        product = next(row for row in values["lines"] if row["kind"] == "product")
        self.assertEqual(product["name"], "Agua x 1.5 Lts")
        self.assertNotIn("[ABC]", product["name"])
        self.assertEqual(product["qty"], "1")
        self.assertTrue(product["show_unit"])
        self.assertTrue(any(row["kind"] == "section" for row in values["lines"]))
        self.assertTrue(any(row["kind"] == "note" for row in values["lines"]))
        self.assertFalse(values["afip_cae"])
        self.assertFalse(values["afip_qr"])

        other = self._customer_invoice()
        html, report_type = self.env["ir.actions.report"]._render_qweb_html(
            "kai_account_invoice_ticket.report_invoice_ticket",
            (invoice | other).ids,
        )
        self.assertEqual(report_type, "html")
        if isinstance(html, bytes):
            html = html.decode()
        self.assertIn("kai-invoice-ticket", html)
        self.assertIn("class=\"article\"", html)
        self.assertIn("border:0", html)
        self.assertIn("Agua x 1.5 Lts", html)
        self.assertNotIn("[ABC]", html)
        self.assertIn("Atendido por:", html)
        self.assertIn("Total", html)
        self.assertIn("Con la tecnología de", html)
        self.assertIn("Odoo", html)
        self.assertIn("Sección ticket", html)
        self.assertIn("Nota ticket", html)
        self.assertIn("Nota:", html)
        self.assertIn("kai-ticket-break", html)
        self.assertNotIn("fa-sticky-note", html)
        self.assertNotIn("fa-tag", html)
        self.assertNotIn("¿Necesita factura?", html)
        self.assertNotIn("afip_auth_code", html)

    def test_qty_is_a_single_token_without_trailing_zeros(self):
        move = self.env["account.move"]
        self.assertEqual(move._kai_ticket_format_qty(1), "1")
        self.assertEqual(move._kai_ticket_format_qty(2.0), "2")
        text = move._kai_ticket_format_qty(0.19)
        self.assertNotIn(" ", text)
        self.assertFalse(text.endswith("0"))
        self.assertTrue(text.startswith("0"))

    def test_tax_group_name_drops_trailing_letter(self):
        move = self.env["account.move"]
        self.assertEqual(move._kai_ticket_trim_tax_name("IVA 21% X"), "IVA 21%")
        self.assertEqual(move._kai_ticket_trim_tax_name("IVA 21%"), "IVA 21%")
        self.assertEqual(move._kai_ticket_trim_tax_name("Perc. IIBB"), "Perc. IIBB")

    def test_fiscal_label_keeps_a_single_currency_symbol(self):
        invoice = self._customer_invoice(post=False)
        symbol = invoice.currency_id.symbol or "$"
        label = invoice._kai_ticket_fiscal_label("IVA Contenido %s" % symbol)
        self.assertEqual(label, "IVA Contenido")
        self.assertNotIn(symbol, label)
