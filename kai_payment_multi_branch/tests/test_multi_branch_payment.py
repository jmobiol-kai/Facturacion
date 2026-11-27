from datetime import timedelta
from unittest.mock import patch

from odoo import SUPERUSER_ID, Command, _, api, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

DEBT_TYPES = ("asset_receivable", "liability_payable")


@tagged("post_install", "-at_install")
class TestMultiBranchPayment(TransactionCase):
    """Pagos y recibos entre sucursales de la misma entidad.

    No hereda ``AccountTestInvoicingCommon``. Esa clase pasa por
    ``BaseCommon``, que cambia el entorno a un usuario de test, y por
    ``UomCommon``, que hace ``uom_dozen.active = True``. En Odoo 19 esa
    asignación llama a ``write()``, y ``write()`` controla el acceso antes
    de mirar si el valor cambió. En 00blinis la escritura de ``uom.uom`` la
    da un grupo custom sin xmlid, no ``uom.group_uom``. ``TransactionCase``
    deja el superusuario y el control de acceso no corre.

    La casa central, Yesterday, Fisa, los diarios, las cuentas, el proveedor
    y el cliente se crean en esta transacción. Cada test arma sus facturas.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = api.Environment(cls.cr, SUPERUSER_ID, {})
        currency = cls.env.ref("base.ARS", raise_if_not_found=False) or cls.env.company.currency_id
        if currency and not currency.active:
            currency.active = True
        country = cls.env.ref("base.ar", raise_if_not_found=False)
        cls.company = cls.env["res.company"].with_context(no_vat_validation=True).create({
            "name": "Casa central test",
            "currency_id": currency.id,
            "country_id": country.id if country else False,
        })
        cls.env = api.Environment(cls.cr, SUPERUSER_ID, {"allowed_company_ids": [cls.company.id]})
        cls.company = cls.env["res.company"].browse(cls.company.id)
        if "use_payment_pro" in cls.company._fields:
            cls.company.use_payment_pro = True
        # CUIT válido. Un dígito verificador mal formado corta el alta de las sucursales.
        cls.company.with_context(no_vat_validation=True).vat = "30111111118"
        cls._identify_as_cuit(cls.company.partner_id)
        responsibility = cls.env.ref("l10n_ar.res_IVARI", raise_if_not_found=False)
        if responsibility and "l10n_ar_afip_responsibility_type_id" in cls.company._fields:
            cls.company.l10n_ar_afip_responsibility_type_id = responsibility
        cls.yesterday = cls._branch("Yesterday")
        cls.fisa = cls._branch("Fisa")
        cls.env.flush_all()
        cls.cr.precommit.run()
        companies = cls.company | cls.yesterday | cls.fisa
        cls.env = api.Environment(cls.cr, SUPERUSER_ID, {"allowed_company_ids": companies.ids})
        cls.company = cls.env["res.company"].browse(cls.company.id)
        cls.yesterday = cls.env["res.company"].browse(cls.yesterday.id)
        cls.fisa = cls.env["res.company"].browse(cls.fisa.id)
        companies = cls.company | cls.yesterday | cls.fisa
        for company in companies:
            if "use_payment_pro" in company._fields:
                company.use_payment_pro = True
            cls._ensure_general_journal(company)
        cls.expense_account = cls._shared_account(companies, "Gastos test", "expense")
        cls.revenue_account = cls._shared_account(companies, "Ingresos test", "income")
        cls.payable_account = cls._shared_account(
            companies, "Proveedores test", "liability_payable", reconcile=True,
        )
        cls.receivable_account = cls._shared_account(
            companies, "Clientes test", "asset_receivable", reconcile=True,
        )
        cls.bank_account = cls._shared_account(companies, "Banco test", "asset_cash")
        journal_vals = {
            "name": "Banco casa central",
            "code": "BCT1",
            "type": "bank",
            "company_id": cls.company.id,
            "default_account_id": cls.bank_account.id,
        }
        journal_model = cls.env["account.journal"]
        if "suspense_account_id" in journal_model._fields:
            journal_vals["suspense_account_id"] = cls._shared_account(
                companies, "Suspenso banco test", "asset_current",
            ).id
        cls.bank_journal = journal_model.with_company(cls.company).create(journal_vals)
        # Sin estas cuentas el pago no puede armar la línea de liquidez.
        outstanding_out = cls._shared_account(
            cls.company, "Pagos pendientes test", "asset_current", reconcile=True,
        )
        outstanding_in = cls._shared_account(
            cls.company, "Cobros pendientes test", "asset_current", reconcile=True,
        )
        if "account_journal_payment_credit_account_id" in cls.company._fields:
            cls.company.account_journal_payment_credit_account_id = outstanding_out
        if "account_journal_payment_debit_account_id" in cls.company._fields:
            cls.company.account_journal_payment_debit_account_id = outstanding_in
        cls.cc_branches = cls._shared_account(
            companies, "Cuenta corriente con sucursales", "asset_current",
        )
        cls.cc_central = cls._shared_account(
            companies, "Cuenta corriente con casa central", "liability_current",
        )
        cls.company.kai_branch_current_account_id = cls.cc_branches
        cls.yesterday.kai_central_current_account_id = cls.cc_central
        cls.fisa.kai_central_current_account_id = cls.cc_central
        cls.vendor = cls.env["res.partner"].create({
            "name": "Proveedor multi sucursal",
            "supplier_rank": 1,
            "company_id": False,
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Cliente multi sucursal",
            "customer_rank": 1,
            "company_id": False,
        })
        cls._identify_as_cuit(cls.vendor, "20111111112")
        cls._identify_as_cuit(cls.customer, "20222222223")
        for company in companies:
            vendor = cls.vendor.with_company(company)
            customer = cls.customer.with_company(company)
            vendor.property_account_payable_id = cls.payable_account
            vendor.property_account_receivable_id = cls.receivable_account
            customer.property_account_payable_id = cls.payable_account
            customer.property_account_receivable_id = cls.receivable_account
        cls.wth_tax = cls.env["account.tax"]
        cls.wth_account = cls.env["account.account"]

    def setUp(self):
        super().setUp()
        companies = self.company | self.yesterday | self.fisa
        self.env = self.env(context=dict(self.env.context, allowed_company_ids=companies.ids))

    @classmethod
    def _shared_account(cls, companies, name, account_type, reconcile=False):
        account_model = cls.env["account.account"].with_company(companies[:1])
        vals = {
            "name": name,
            "code": account_model._search_new_account_code("91100000", cache=set()),
            "account_type": account_type,
            "company_ids": [Command.set(companies.ids)],
        }
        if reconcile:
            vals["reconcile"] = True
        return account_model.create(vals)

    @classmethod
    def _identify_as_cuit(cls, partner, vat=None):
        """Deja el contacto como CUIT cuando la localización argentina está instalada."""
        if "l10n_latam_identification_type_id" not in partner._fields:
            if vat:
                partner.with_context(no_vat_validation=True).vat = vat
            return
        cuit = cls.env.ref("l10n_ar.it_cuit", raise_if_not_found=False)
        vals = {}
        if cuit:
            vals["l10n_latam_identification_type_id"] = cuit.id
        if vat:
            vals["vat"] = vat
        responsibility = cls.env.ref("l10n_ar.res_IVARI", raise_if_not_found=False)
        if responsibility and "l10n_ar_afip_responsibility_type_id" in partner._fields:
            vals["l10n_ar_afip_responsibility_type_id"] = responsibility.id
        if vals:
            partner.with_context(no_vat_validation=True).write(vals)

    @classmethod
    def _branch(cls, name):
        vals = {
            "name": name,
            "parent_id": cls.company.id,
            "vat": cls.company.vat,
            "currency_id": cls.company.currency_id.id,
            "country_id": cls.company.country_id.id,
        }
        if (
            "l10n_ar_afip_responsibility_type_id" in cls.env["res.company"]._fields
            and cls.company.l10n_ar_afip_responsibility_type_id
        ):
            vals["l10n_ar_afip_responsibility_type_id"] = cls.company.l10n_ar_afip_responsibility_type_id.id
        company = cls.env["res.company"].with_context(no_vat_validation=True).create(vals)
        cls._identify_as_cuit(company.partner_id)
        return company

    @classmethod
    def _ensure_general_journal(cls, company):
        journal = cls.env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "general"),
        ], limit=1)
        if not journal:
            cls.env["account.journal"].create({
                "name": "Operaciones varias",
                "code": "OPV",
                "type": "general",
                "company_id": company.id,
            })

    def _invoice_journal(self, company, move_type):
        """Diario sin comprobantes fiscales.

        En el plan argentino los diarios de compra y venta exigen tipo y número
        de documento y un IVA por línea. Estos tests miden la deuda, no el
        comprobante, así que usan un diario que no documenta.
        """
        journal_model = self.env["account.journal"]
        journal_type = "purchase" if move_type in ("in_invoice", "in_refund") else "sale"
        domain = [
            ("company_id", "=", company.id),
            ("type", "=", journal_type),
        ]
        if (
            "l10n_latam_use_documents" in journal_model._fields
            and company._localization_use_documents()
        ):
            domain.append(("l10n_latam_use_documents", "=", False))
        journal = journal_model.search(domain, limit=1)
        if journal:
            return journal
        vals = {
            "name": "Compras internas" if journal_type == "purchase" else "Ventas internas",
            "code": "CINT" if journal_type == "purchase" else "VINT",
            "type": journal_type,
            "company_id": company.id,
        }
        if "l10n_latam_use_documents" in journal_model._fields:
            vals["l10n_latam_use_documents"] = False
        return journal_model.with_company(company).create(vals)

    def _invoice(self, company, move_type, partner, amount):
        account = (
            self.expense_account
            if move_type in ("in_invoice", "in_refund")
            else self.revenue_account
        )
        vals = {
            "move_type": move_type,
            "company_id": company.id,
            "partner_id": partner.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({
                "name": "Servicio",
                "quantity": 1,
                "price_unit": amount,
                "account_id": account.id,
                "tax_ids": [Command.clear()],
            })],
        }
        journal = self._invoice_journal(company, move_type)
        if journal:
            vals["journal_id"] = journal.id
        move = self.env["account.move"].with_company(company).create(vals)
        move.action_post()
        return move

    def _debt_lines(self, moves):
        return moves.line_ids.filtered(lambda line: line.account_id.account_type in DEBT_TYPES)

    def _payment(self, partner_type, partner, lines):
        amount = abs(sum(lines.mapped("amount_residual")))
        payment_type = "outbound" if partner_type == "supplier" else "inbound"
        payment = self.env["account.payment"].with_company(self.company).create({
            "payment_type": payment_type,
            "partner_type": partner_type,
            "partner_id": partner.id,
            "amount": amount,
            "currency_id": self.company.currency_id.id,
            "journal_id": self.bank_journal.id,
            "company_id": self.company.id,
        })
        payment.to_pay_move_line_ids = [Command.set(lines.ids)]
        return payment

    def _manual_method_line(self):
        line = self.bank_journal.outbound_payment_method_line_ids.filtered(lambda item: item.code == "manual")[:1]
        if line:
            return line
        method = self.env.ref("account.account_payment_method_manual_out")
        return self.env["account.payment.method.line"].create({
            "name": "Manual",
            "payment_method_id": method.id,
            "journal_id": self.bank_journal.id,
        })

    def _outstanding_account(self):
        line = self.bank_journal.outbound_payment_method_line_ids.filtered("payment_account_id")[:1]
        if line:
            return line.payment_account_id
        return self.env["account.account"].with_company(self.company).create({
            "name": "Cheques propios a debitar",
            "code": self.env["account.account"].with_company(self.company)._search_new_account_code(
                "91180000", cache=set(),
            ),
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set(self.company.ids)],
        })

    def _own_checks_method_line(self):
        line = self.bank_journal.outbound_payment_method_line_ids.filtered(lambda item: item.code == "own_checks")[:1]
        if not line:
            method = self.env["account.payment.method"].search([
                ("code", "=", "own_checks"),
                ("payment_type", "=", "outbound"),
            ], limit=1)
            self.assertTrue(method, "El método own_checks tiene que estar instalado")
            line = self.env["account.payment.method.line"].create({
                "name": "Cheques propios",
                "payment_method_id": method.id,
                "journal_id": self.bank_journal.id,
            })
        if not line.payment_account_id:
            line.payment_account_id = self._outstanding_account()
        return line

    def _use_own_checks(self, payment, checks):
        """checks: lista de (fecha de vencimiento, importe). El amount del pago queda en la suma."""
        payment.payment_method_line_id = self._own_checks_method_line()
        payment.l10n_latam_new_check_ids = [
            Command.clear(),
            *[
                Command.create({
                    "name": str(10000000 + index),
                    "payment_date": due,
                    "amount": amount,
                })
                for index, (due, amount) in enumerate(checks, start=1)
            ],
        ]
        return payment

    def _check_bank(self):
        bank = self.env["res.bank"].search([("name", "=", "Banco cheques test")], limit=1)
        if not bank:
            bank = self.env["res.bank"].create({"name": "Banco cheques test"})
        return bank

    def _method_line(self, journal, code):
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        return lines.filtered(lambda line: line.code == code)[:1]

    def _portfolio_journal(self):
        """Cartera de la casa central: recibe cheques de terceros y los endosa."""
        journal = getattr(self, "checks_journal", self.env["account.journal"])
        if journal:
            return journal
        account = self.env["account.account"].with_company(self.company).create({
            "name": "Cheques de terceros en cartera",
            "code": self.env["account.account"].with_company(self.company)._search_new_account_code(
                "11130000", cache=set(),
            ),
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set(self.company.ids)],
        })
        self.third_party_account = account
        journal = self.env["account.journal"].create({
            "name": "Cartera cheques de terceros",
            "code": "CH3",
            "type": "cash",
            "company_id": self.company.id,
        })
        specs = (
            ("new_third_party_checks", "inbound", "Nuevo cheque de terceros"),
            ("in_third_party_checks", "inbound", "Cheque de terceros en cartera"),
            ("out_third_party_checks", "outbound", "Endoso de cheque de terceros"),
        )
        for code, payment_type, label in specs:
            method = self.env["account.payment.method"].search([
                ("code", "=", code),
                ("payment_type", "=", payment_type),
            ], limit=1)
            self.assertTrue(method, code)
            field = (
                "inbound_payment_method_line_ids"
                if payment_type == "inbound"
                else "outbound_payment_method_line_ids"
            )
            journal.write({
                field: [Command.create({
                    "name": label,
                    "payment_method_id": method.id,
                    "payment_account_id": account.id,
                })],
            })
        self.checks_journal = journal
        return journal

    def _receive_customer_checks(self, checks):
        """El cliente entrega cheques nuevos en la casa central y quedan en cartera.

        El alta del cheque es ``new_third_party_checks``: ``in_third_party_checks``
        mueve cheques que ya existen. El diario igual tiene ese método, que es
        el de la cartera.
        """
        journal = self._portfolio_journal()
        self.assertTrue(self._method_line(journal, "in_third_party_checks"))
        total = sum(amount for _due, amount, _number in checks)
        invoice = self._invoice(self.company, "out_invoice", self.customer, total)
        payment = self.env["account.payment"].with_company(self.company).create({
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.customer.id,
            "amount": total,
            "currency_id": self.company.currency_id.id,
            "journal_id": journal.id,
            "company_id": self.company.id,
            "payment_method_line_id": self._method_line(journal, "new_third_party_checks").id,
            "l10n_latam_new_check_ids": [
                Command.create({
                    "name": number,
                    "payment_date": due,
                    "amount": amount,
                    "bank_id": self._check_bank().id,
                })
                for due, amount, number in checks
            ],
        })
        payment.to_pay_move_line_ids = [Command.set(self._debt_lines(invoice).ids)]
        payment.action_post()
        received = payment.l10n_latam_new_check_ids
        received.invalidate_recordset()
        self.assertEqual(set(received.mapped("current_journal_id").ids), {journal.id})
        self._assert_move_balanced(payment.move_id)
        return received

    def _endorse_checks(self, payment, checks):
        journal = checks.current_journal_id
        self.assertEqual(len(journal), 1)
        payment.journal_id = journal
        payment.payment_method_line_id = self._method_line(journal, "out_third_party_checks")
        payment.l10n_latam_move_check_ids = [Command.set(checks.ids)]
        return payment

    def _assert_third_party_credited(self, payment, checks):
        account = self.third_party_account
        lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == account)
        self.assertEqual(len(lines), len(checks))
        self.assertTrue(all(line.company_id == payment.company_id for line in lines))
        self.assertAlmostEqual(sum(lines.mapped("balance")), -sum(checks.mapped("amount")), places=2)
        for check in checks:
            line = lines.filtered(lambda candidate, check=check: check in candidate.l10n_latam_check_ids)
            self.assertEqual(len(line), 1)
            self.assertAlmostEqual(line.balance, -check.amount, places=2)
            self.assertEqual(line.date_maturity, check.payment_date)

    def _assert_checks_in_hand(self, checks, journal):
        checks.invalidate_recordset(["current_journal_id"])
        self.assertEqual(set(checks.mapped("current_journal_id").ids), {journal.id})

    def _assert_checks_endorsed(self, checks):
        checks.invalidate_recordset(["current_journal_id"])
        self.assertFalse(any(checks.mapped("current_journal_id")))

    def _ensure_withholding_tax(self):
        """Impuesto mínimo de ganancias. No usa el padrón ni la posición fiscal."""
        if self.wth_tax:
            return self.wth_tax
        companies = self.company | self.yesterday | self.fisa
        account_model = self.env["account.account"].with_company(self.company)
        self.wth_account = account_model.create({
            "name": "Retención ganancias a depositar",
            "code": account_model._search_new_account_code("21420000", cache=set()),
            "account_type": "liability_current",
            "reconcile": False,
            "company_ids": [Command.set(self.company.ids)],
        })
        base_account = account_model.create({
            "name": "Base de retenciones",
            "code": account_model._search_new_account_code("21420000", cache=set()),
            "account_type": "liability_current",
            "reconcile": False,
            "company_ids": [Command.set(companies.ids)],
        })
        if "l10n_ar_tax_base_account_id" in self.company._fields:
            self.company.l10n_ar_tax_base_account_id = base_account
        sequence = self.env["ir.sequence"].create({
            "name": "Retención ganancias test",
            "implementation": "standard",
            "padding": 8,
            "prefix": "RG-",
            "company_id": self.company.id,
        })
        tax_group = self.env["account.tax.group"].search([
            ("company_id", "=", self.company.id),
        ], limit=1)
        if not tax_group:
            tax_group = self.env["account.tax.group"].create({
                "name": "Retenciones",
                "company_id": self.company.id,
            })
        def repartition():
            return [
                Command.create({"repartition_type": "base", "factor_percent": 100.0}),
                Command.create({
                    "repartition_type": "tax",
                    "factor_percent": 100.0,
                    "account_id": self.wth_account.id,
                }),
            ]

        vals = {
            "name": "Retención ganancias test",
            "amount_type": "percent",
            "amount": 10.0,
            "type_tax_use": "none",
            "company_id": self.company.id,
            "tax_group_id": tax_group.id,
            "l10n_ar_tax_type": "earnings",
            "l10n_ar_withholding_payment_type": "supplier",
            "l10n_ar_withholding_sequence_id": sequence.id,
            "l10n_ar_non_taxable_amount": 0.0,
            "l10n_ar_minimum_threshold": 0.0,
            "invoice_repartition_line_ids": repartition(),
            "refund_repartition_line_ids": repartition(),
        }
        if "l10n_ar_code" in self.env["account.tax"]._fields:
            vals["l10n_ar_code"] = "KAI99"
        if "ratio" in self.env["account.tax"]._fields:
            vals["ratio"] = 100.0
        self.wth_tax = self.env["account.tax"].create(vals)
        return self.wth_tax

    def _add_withholding(self, payment):
        tax = self._ensure_withholding_tax()
        payment.l10n_ar_withholding_line_ids = [Command.create({
            "tax_id": tax.id,
            "name": "RG-00000001",
        })]
        payment.invalidate_recordset()
        withholding = payment.l10n_ar_withholding_line_ids
        self.assertTrue(withholding)
        self.assertGreater(withholding.amount, 0.0)
        return withholding

    def _assert_move_balanced(self, move):
        move.line_ids.invalidate_recordset()
        self.assertTrue(move.company_currency_id.is_zero(sum(move.line_ids.mapped("balance"))))

    def _assert_check_lines(self, payment):
        checks = payment.l10n_latam_new_check_ids
        self.assertGreaterEqual(len(checks), 2)
        self.assertGreater(len(set(checks.mapped("payment_date"))), 1)
        for check in checks:
            lines = payment.move_id.line_ids.filtered(lambda line, check=check: check in line.l10n_latam_check_ids)
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines.date_maturity, check.payment_date)
            self.assertAlmostEqual(lines.balance, -check.amount, places=2)
            self.assertEqual(lines.company_id, payment.company_id)

    def _assert_branch_gross(self, payment, bills):
        """Cada sucursal se cancela por el bruto y la cuenta corriente no se lleva la retención."""
        currency = payment.company_currency_id
        self.assertEqual(payment.move_id.company_id, self.company)
        self._assert_move_balanced(payment.move_id)
        for bill in bills:
            gross = abs(bill.amount_total_signed)
            company = bill.company_id
            cc_lines = payment.move_id.line_ids.filtered(
                lambda line, company=company: line.account_id == self.cc_branches and line.kai_settled_company_id == company
            )
            self.assertEqual(len(cc_lines), 1)
            self.assertAlmostEqual(cc_lines.balance, gross, places=2)
            bridge = payment.kai_bridge_move_ids.filtered(lambda move, company=company: move.company_id == company)
            self.assertEqual(len(bridge), 1)
            self._assert_move_balanced(bridge)
            payable = bridge.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
            central = bridge.line_ids.filtered(lambda line: line.account_id == self.cc_central)
            self.assertAlmostEqual(sum(payable.mapped("balance")), gross, places=2)
            self.assertAlmostEqual(sum(central.mapped("balance")), -gross, places=2)
            self._assert_reconciled(self._debt_lines(bill))
            self.assertAlmostEqual(
                self._partner_balance(company, bill.partner_id, "liability_payable"),
                0.0,
                places=2,
            )
        if self.wth_account:
            self.assertFalse(payment.kai_bridge_move_ids.line_ids.filtered(
                lambda line: line.account_id == self.wth_account
            ))
        gross_total = currency.round(sum(abs(bill.amount_total_signed) for bill in bills))
        cc_total = currency.round(sum(payment.move_id.line_ids.filtered(
            lambda line: line.account_id == self.cc_branches
        ).mapped("balance")))
        self.assertAlmostEqual(cc_total, gross_total, places=2)

    def _assert_withholding_on_payer(self, payment, gross):
        lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.wth_account)
        self.assertTrue(lines)
        withheld = payment.company_currency_id.round(-sum(lines.mapped("balance")))
        self.assertGreater(withheld, 0.0)
        self.assertAlmostEqual(self._liquidity_balance(payment), -(gross - withheld), places=2)
        self.assertAlmostEqual(payment.amount, gross - withheld, places=2)
        return withheld

    def _legacy_payment(self, partner_type, partner, lines):
        payment = self._payment(partner_type, partner, lines)
        payment.with_context(kai_skip_branch_bridge=True).action_post()
        return payment

    def _wizard(self, payments):
        return self.env["kai.payment.regularize.wizard"].with_context(
            active_model="account.payment",
            active_ids=payments.ids,
        ).create({})

    def _partner_balance(self, company, partner, account_type):
        lines = self.env["account.move.line"].search([
            ("company_id", "=", company.id),
            ("partner_id", "=", partner.commercial_partner_id.id),
            ("account_id.account_type", "=", account_type),
            ("parent_state", "=", "posted"),
        ])
        return sum(lines.mapped("balance"))

    def _account_balance(self, account, company):
        lines = self.env["account.move.line"].search([
            ("account_id", "=", account.id),
            ("company_id", "=", company.id),
            ("parent_state", "=", "posted"),
        ])
        return sum(lines.mapped("balance"))

    def _liquidity_balance(self, payment):
        accounts = payment.outstanding_account_id | payment.journal_id.default_account_id
        lines = payment.move_id.line_ids.filtered(lambda line: line.account_id in accounts)
        return sum(lines.mapped("balance"))

    def _assert_reconciled(self, lines):
        lines.invalidate_recordset()
        self.assertTrue(lines)
        self.assertTrue(all(lines.mapped("reconciled")))
        self.assertTrue(self.company.currency_id.is_zero(sum(lines.mapped("amount_residual"))))

    def test_supplier_payment_clears_central_and_two_branches(self):
        central_bill = self._invoice(self.company, "in_invoice", self.vendor, 80)
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(central_bill | yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()

        self.assertEqual(payment.move_id.state, "posted")
        self.assertEqual(payment.move_id.company_id, self.company)
        self._assert_reconciled(lines)
        self.assertEqual(set(payment.kai_bridge_move_ids.company_id.ids), {self.yesterday.id, self.fisa.id})
        cc_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
        self.assertEqual(set(cc_lines.mapped("kai_settled_company_id").ids), {self.yesterday.id, self.fisa.id})
        self.assertAlmostEqual(sum(cc_lines.mapped("balance")), 150.0, places=2)
        payable_lines = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        )
        self.assertEqual(len(payable_lines), 1)
        self.assertAlmostEqual(payable_lines.balance, 80.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), 0.0, places=2)
        central_cc = self._account_balance(self.cc_branches, self.company)
        branch_cc = (
            self._account_balance(self.cc_central, self.yesterday)
            + self._account_balance(self.cc_central, self.fisa)
        )
        self.assertAlmostEqual(central_cc, 150.0, places=2)
        self.assertAlmostEqual(branch_cc, -150.0, places=2)
        self.assertAlmostEqual(central_cc + branch_cc, 0.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -230.0, places=2)

        payment.action_draft()
        lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(lines.mapped("reconciled")))
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 0.0, places=2)

        payment.action_post()
        self._assert_reconciled(lines)
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), 0.0, places=2)

    def test_customer_receipt_and_cancel(self):
        yesterday_invoice = self._invoice(self.yesterday, "out_invoice", self.customer, 120)
        fisa_invoice = self._invoice(self.fisa, "out_invoice", self.customer, 80)
        lines = self._debt_lines(yesterday_invoice | fisa_invoice)
        payment = self._payment("customer", self.customer, lines)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ))
        cc_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
        self.assertAlmostEqual(sum(cc_lines.mapped("balance")), -200.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.customer, "asset_receivable"), 0.0, places=2)
        central_cc = self._account_balance(self.cc_branches, self.company)
        branch_cc = (
            self._account_balance(self.cc_central, self.yesterday)
            + self._account_balance(self.cc_central, self.fisa)
        )
        self.assertAlmostEqual(central_cc, -200.0, places=2)
        self.assertAlmostEqual(central_cc + branch_cc, 0.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), 200.0, places=2)

        payment.action_cancel()
        lines.invalidate_recordset()
        self.assertEqual(payment.state, "canceled")
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(lines.mapped("reconciled")))
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 120.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.customer, "asset_receivable"), 80.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 0.0, places=2)

    def test_supplier_credit_note_nets_inside_the_branch(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        refund = self._invoice(self.yesterday, "in_refund", self.vendor, 30)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(bill | refund | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        self.assertAlmostEqual(payment.amount, 120.0, places=2)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 120.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -70.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.fisa), -50.0, places=2)
        yesterday_bridge = payment.kai_bridge_move_ids.filtered(lambda move: move.company_id == self.yesterday)
        self.assertEqual(len(yesterday_bridge), 1)
        self.assertTrue(yesterday_bridge.line_ids.filtered(lambda line: line.kai_debt_move_line_id == self._debt_lines(bill)))
        self.assertTrue(yesterday_bridge.line_ids.filtered(lambda line: line.kai_debt_move_line_id == self._debt_lines(refund)))

    def test_single_company_payment_has_no_bridge(self):
        bill = self._invoice(self.company, "in_invoice", self.vendor, 90)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches))
        payable = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        self.assertEqual(len(payable), 1)
        self.assertAlmostEqual(payable.balance, 90.0, places=2)
        self.assertEqual(payable.partner_id, self.vendor.commercial_partner_id)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)

    def test_other_legal_entity_is_rejected(self):
        other = self.env["res.company"].with_context(no_vat_validation=True).create({
            "name": "Otra SA",
            "parent_id": self.company.id,
            "vat": "30987654321",
            "country_id": self.company.country_id.id,
        })
        self.cr.precommit.run()
        self._ensure_general_journal(other)
        own_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 40)
        foreign_bill = self._invoice(other, "in_invoice", self.vendor, 25)
        lines = self._debt_lines(own_bill | foreign_bill)
        payment = self._payment("supplier", self.vendor, self._debt_lines(own_bill))
        self.assertFalse(payment._kai_shares_legal_entity(lines.company_id))
        with self.assertRaises(UserError):
            payment.to_pay_move_line_ids = [Command.set(lines.ids)]
        self.assertNotIn(other, payment.to_pay_move_line_ids.company_id)

    def test_missing_branch_account_blocks_confirmation(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 40)
        other = self._invoice(self.fisa, "in_invoice", self.vendor, 10)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill | other))
        self.company.kai_branch_current_account_id = False
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("sucursales", str(error.exception))
        self.assertFalse(payment.kai_bridge_move_ids)

    def test_partial_payment_is_blocked(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        other = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill | other))
        payment.amount = 120
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("parciales", str(error.exception))
        self.assertNotIn("anticipos", str(error.exception).lower())

    def test_foreign_currency_is_blocked(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 40)
        other = self._invoice(self.fisa, "in_invoice", self.vendor, 10)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill | other))
        euro = self.env.ref("base.EUR")
        euro.active = True
        payment.currency_id = euro
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("moneda", str(error.exception))

    def test_own_checks_across_two_branches(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 80)
        lines = self._debt_lines(yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        today = fields.Date.today()
        self._use_own_checks(payment, [
            (today, 70),
            (today + timedelta(days=15), 60),
            (today + timedelta(days=45), 50),
        ])
        self.assertAlmostEqual(payment.amount, 180.0, places=2)
        payment.action_post()

        self._assert_check_lines(payment)
        self._assert_branch_gross(payment, yesterday_bill | fisa_bill)
        self.assertAlmostEqual(self._liquidity_balance(payment), -180.0, places=2)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))

    def test_manual_payment_withholding_across_two_branches(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.payment_method_line_id = self._manual_method_line()
        withholding = self._add_withholding(payment)
        payment.amount = 150.0 - withholding.amount
        payment.action_post()

        self._assert_branch_gross(payment, yesterday_bill | fisa_bill)
        self._assert_withholding_on_payer(payment, 150.0)
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)

    def test_own_checks_with_withholding_draft_repost_and_cancel(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 120)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 80)
        bills = yesterday_bill | fisa_bill
        lines = self._debt_lines(bills)
        payment = self._payment("supplier", self.vendor, lines)
        today = fields.Date.today()
        # El método de pago se define antes de la retención: cambiarlo
        # recomputa la posición fiscal y l10n_ar_tax vacía las retenciones.
        self._use_own_checks(payment, [
            (today, 120),
            (today + timedelta(days=30), 80),
        ])
        withholding = self._add_withholding(payment)
        net = payment.company_currency_id.round(200.0 - withholding.amount)
        first = payment.company_currency_id.round(net * 0.6)
        second = payment.company_currency_id.round(net - first)
        checks = payment.l10n_latam_new_check_ids.sorted("id")
        checks[0].amount = first
        checks[1].amount = second
        payment.action_post()

        self._assert_check_lines(payment)
        self._assert_branch_gross(payment, bills)
        withheld = self._assert_withholding_on_payer(payment, 200.0)
        self.assertAlmostEqual(withheld, withholding.amount, places=2)

        payment.action_draft()
        lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(lines.mapped("reconciled")))
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -120.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 0.0, places=2)

        payment.action_post()
        self._assert_check_lines(payment)
        self._assert_branch_gross(payment, bills)
        self._assert_withholding_on_payer(payment, 200.0)
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)

        payment.action_cancel()
        lines.invalidate_recordset()
        self.assertEqual(payment.state, "canceled")
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(lines.mapped("reconciled")))
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -120.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), -80.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 0.0, places=2)

    def _assert_endorsement_cycle(self, payment, checks, bills, journal):
        self._assert_checks_endorsed(checks)
        self._assert_branch_gross(payment, bills)
        self._assert_third_party_credited(payment, checks)

        payment.action_draft()
        bills_lines = self._debt_lines(bills)
        bills_lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(bills_lines.mapped("reconciled")))
        self._assert_checks_in_hand(checks, journal)

        payment.action_post()
        self._assert_checks_endorsed(checks)
        self._assert_branch_gross(payment, bills)
        self._assert_third_party_credited(payment, checks)
        self.assertEqual(len(payment.kai_bridge_move_ids), len(bills))

        payment.action_cancel()
        bills_lines.invalidate_recordset()
        self.assertEqual(payment.state, "canceled")
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(bills_lines.mapped("reconciled")))
        self._assert_checks_in_hand(checks, journal)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 0.0, places=2)

    def test_third_party_checks_across_two_branches(self):
        today = fields.Date.today()
        checks = self._receive_customer_checks([
            (today, 70, "00000031"),
            (today + timedelta(days=20), 60, "00000032"),
            (today + timedelta(days=40), 50, "00000033"),
        ])
        journal = checks.current_journal_id
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 80)
        bills = yesterday_bill | fisa_bill
        payment = self._payment("supplier", self.vendor, self._debt_lines(bills))
        self._endorse_checks(payment, checks)
        self.assertAlmostEqual(payment.amount, 180.0, places=2)
        payment.action_post()

        self._assert_endorsement_cycle(payment, checks, bills, journal)

    def test_third_party_checks_with_withholding_across_two_branches(self):
        today = fields.Date.today()
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 120)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 80)
        bills = yesterday_bill | fisa_bill
        payment = self._payment("supplier", self.vendor, self._debt_lines(bills))
        payment.payment_method_line_id = self._manual_method_line()
        withholding = self._add_withholding(payment)
        net = payment.company_currency_id.round(200.0 - withholding.amount)
        first = payment.company_currency_id.round(net * 0.4)
        second = payment.company_currency_id.round(net - first)
        checks = self._receive_customer_checks([
            (today, first, "00000041"),
            (today + timedelta(days=25), second, "00000042"),
        ])
        journal = checks.current_journal_id
        # Cambiar el método vacía la retención: se vuelve a cargar después.
        self._endorse_checks(payment, checks)
        withholding = self._add_withholding(payment)
        self.assertAlmostEqual(payment.amount, net, places=2)
        self.assertAlmostEqual(withholding.amount, 200.0 - net, places=2)
        payment.action_post()

        self._assert_withholding_on_payer(payment, 200.0)
        self._assert_endorsement_cycle(payment, checks, bills, journal)
        self.assertFalse(payment.kai_bridge_move_ids.line_ids.filtered(
            lambda line: line.account_id == self.wth_account
        ))

    def test_single_branch_payment_clears_the_branch_not_the_central_payable(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertEqual(payment.kai_bridge_move_ids.company_id, self.yesterday)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))
        cc_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
        self.assertEqual(cc_lines.kai_settled_company_id, self.yesterday)
        self.assertAlmostEqual(cc_lines.balance, 100.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -100.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -100.0, places=2)

    def test_regularize_legacy_supplier_matches_a_new_payment(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._legacy_payment("supplier", self.vendor, lines)
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertTrue(lines.reconciled)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 100.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)
        liquidity_before = self._liquidity_balance(payment)

        wizard = self._wizard(payment)
        self.assertTrue(wizard.line_ids.eligible)
        self.assertEqual(wizard.date, payment.date)
        entry_date = payment.date + timedelta(days=4)
        wizard.date = entry_date
        wizard.action_regularize()

        lines.invalidate_recordset()
        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -100.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), liquidity_before, places=2)
        self.assertTrue(payment.kai_bridge_move_ids)
        self.assertEqual(set(payment.kai_bridge_move_ids.mapped("date")), {entry_date})
        self.assertEqual(set(payment.kai_bridge_move_ids.company_id), {self.company, self.yesterday})
        branch_match = (lines.matched_debit_ids | lines.matched_credit_ids)
        branch_counterparts = (branch_match.debit_move_id | branch_match.credit_move_id) - lines
        self.assertTrue(branch_counterparts)
        self.assertTrue(all(line.company_id == self.yesterday for line in branch_counterparts))
        self.assertTrue(all(line.move_id.kai_origin_payment_id == payment for line in branch_counterparts))
        payable = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        payable_match = (payable.matched_debit_ids | payable.matched_credit_ids)
        payable_counterparts = (payable_match.debit_move_id | payable_match.credit_move_id) - payable
        self.assertTrue(all(line.company_id == self.company for line in payable_counterparts))
        self.assertTrue(all(line.move_id.kai_origin_payment_id == payment for line in payable_counterparts))

        again = self._wizard(payment)
        self.assertFalse(again.line_ids.eligible)
        self.assertIn("regularizado", again.line_ids.reason)
        with self.assertRaises(UserError):
            again.action_regularize()
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)

        payment.action_draft()
        lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(lines.reconciled)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)

        payment.action_post()
        self._assert_reconciled(lines)
        self.assertEqual(payment.kai_bridge_move_ids.company_id, self.yesterday)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -100.0, places=2)

    def test_regularize_legacy_customer_receipt(self):
        invoice = self._invoice(self.yesterday, "out_invoice", self.customer, 80)
        lines = self._debt_lines(invoice)
        payment = self._legacy_payment("customer", self.customer, lines)
        self.assertAlmostEqual(self._partner_balance(self.company, self.customer, "asset_receivable"), -80.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 80.0, places=2)

        wizard = self._wizard(payment)
        self.assertEqual(wizard.date, payment.date)
        wizard.action_regularize()

        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._partner_balance(self.company, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), -80.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 80.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), 80.0, places=2)

    def test_regularize_wizard_previews_and_skips_rejected_payments(self):
        branch_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 60)
        own_bill = self._invoice(self.company, "in_invoice", self.vendor, 25)
        legacy = self._legacy_payment("supplier", self.vendor, self._debt_lines(branch_bill))
        own_payment = self._payment("supplier", self.vendor, self._debt_lines(own_bill))
        own_payment.action_post()

        wizard = self._wizard(legacy | own_payment)
        self.assertEqual(len(wizard.line_ids), 2)
        by_payment = {line.payment_id: line for line in wizard.line_ids}
        self.assertTrue(by_payment[legacy].eligible)
        self.assertFalse(by_payment[own_payment].eligible)
        self.assertIn("otra sucursal", by_payment[own_payment].reason)
        other_vendor = self.env["res.partner"].create({"name": "Proveedor bloqueado"})
        blocked = self._legacy_payment(
            "supplier",
            other_vendor,
            self._debt_lines(self._invoice(self.fisa, "in_invoice", other_vendor, 15)),
        )
        euro = self.env.ref("base.EUR")
        euro.active = True
        blocked.currency_id = euro
        self.assertIn("moneda extranjera", blocked._kai_regularize_block_reason())
        blocked.currency_id = self.company.currency_id
        with patch.object(type(blocked), "_kai_has_withholdings", lambda self: True):
            self.assertIn("retenciones", blocked._kai_regularize_block_reason())
        method = self.env["account.payment.method"].search([("code", "=", "own_checks")], limit=1)
        self.assertTrue(method)
        blocked.payment_method_line_id = self.env["account.payment.method.line"].create({
            "name": "Cheque propio",
            "payment_method_id": method.id,
            "journal_id": blocked.journal_id.id,
        })
        self.assertIn("cheques", blocked._kai_regularize_block_reason())

        wizard.action_regularize()
        self.assertTrue(legacy.kai_bridge_move_ids)
        self.assertFalse(own_payment.kai_bridge_move_ids)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)

    def test_bridge_journal_uses_miscellaneous_not_point_of_sale(self):
        company = self.yesterday
        self.env["account.journal"].create({
            "name": "Punto de venta",
            "code": "POSS",
            "type": "general",
            "company_id": company.id,
            "sequence": 1,
        })
        misce = self.env["account.journal"].create({
            "name": "Operaciones misceláneas",
            "code": "MISCE",
            "type": "general",
            "company_id": company.id,
            "sequence": 50,
        })
        chart = self.env["account.payment"]._kai_default_misc_journal(company)
        if chart and chart != misce:
            chart.active = False
        self.assertEqual(self.env["account.payment"]._kai_default_misc_journal(company), misce)

        bill = self._invoice(company, "in_invoice", self.vendor, 40)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill))
        payment.action_post()
        self.assertEqual(payment.kai_bridge_move_ids.journal_id, misce)

        custom = self.env["account.journal"].create({
            "name": "Puentes sucursal",
            "code": "PUEN",
            "type": "general",
            "company_id": company.id,
        })
        company.kai_bridge_journal_id = custom
        other = self._invoice(company, "in_invoice", self.vendor, 15)
        second = self._payment("supplier", self.vendor, self._debt_lines(other))
        second.action_post()
        self.assertEqual(second.kai_bridge_move_ids.journal_id, custom)

        self.fisa.kai_bridge_journal_id = False
        for journal in self.env["account.journal"].search([
            ("company_id", "=", self.fisa.id),
            ("type", "=", "general"),
        ]):
            if not self.env["account.payment"]._kai_journal_is_reserved(journal):
                journal.active = False
        self.env["account.journal"].create({
            "name": "Punto de venta",
            "code": "POSS",
            "type": "general",
            "company_id": self.fisa.id,
            "sequence": 1,
        })
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 10)
        blocked = self._payment("supplier", self.vendor, self._debt_lines(fisa_bill))
        with self.assertRaises(UserError) as error:
            blocked.action_post()
        self.assertIn("misceláneas", str(error.exception))

    def _assert_paid_tab(self, payment, lines):
        payment.invalidate_recordset(["matched_move_line_ids", "matched_amount", "unmatched_amount"])
        self.assertEqual(set(payment.matched_move_line_ids.ids), set(lines.ids))
        self.assertAlmostEqual(payment.matched_amount, payment.amount, places=2)
        self.assertTrue(payment.currency_id.is_zero(payment.unmatched_amount))

    def test_paid_tab_includes_branch_invoices(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()
        self._assert_paid_tab(payment, lines)

        payment.action_draft()
        payment.invalidate_recordset(["matched_move_line_ids", "matched_amount", "unmatched_amount"])
        self.assertFalse(payment.matched_move_line_ids)
        self.assertTrue(payment.currency_id.is_zero(payment.matched_amount))
        self.assertTrue(payment.currency_id.is_zero(payment.unmatched_amount))
        self.assertFalse(lines.kai_settled_by_company_ids)

    def test_mixed_payment_covers_central_debt_without_payment_pro_helper(self):
        """account_payment_pro 766c676 no tiene ``_get_debt_lines_covered``.

        Un pago que mezcla la casa central y una sucursal igual tiene que
        conciliar solo la deuda de la central, en el orden y por los importes
        que cubre el apunte del pago.
        """
        central_bill = self._invoice(self.company, "in_invoice", self.vendor, 80)
        branch_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 120)
        lines = self._debt_lines(central_bill | branch_bill)
        payment = self._payment("supplier", self.vendor, lines)
        removed = []
        for model_cls in type(payment).mro():
            if "_get_debt_lines_covered" in model_cls.__dict__:
                removed.append((model_cls, model_cls.__dict__["_get_debt_lines_covered"]))
                delattr(model_cls, "_get_debt_lines_covered")
        self.assertFalse(any("_get_debt_lines_covered" in cls.__dict__ for cls in type(payment).mro()))
        try:
            payment.action_post()
        finally:
            for model_cls, method in removed:
                setattr(model_cls, "_get_debt_lines_covered", method)

        self._assert_reconciled(lines)
        self.assertEqual(payment.kai_bridge_move_ids.company_id, self.yesterday)
        payable = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        )
        self.assertEqual(len(payable), 1)
        self.assertAlmostEqual(payable.balance, 80.0, places=2)
        self.assertTrue(self._debt_lines(central_bill).reconciled)
        central_match = self._debt_lines(central_bill).matched_debit_ids | self._debt_lines(central_bill).matched_credit_ids
        central_counterparts = (central_match.debit_move_id | central_match.credit_move_id) - self._debt_lines(central_bill)
        self.assertTrue(central_counterparts)
        self.assertTrue(all(line.move_id == payment.move_id for line in central_counterparts))
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)

    def test_paid_tab_mixed_central_and_branch(self):
        central_bill = self._invoice(self.company, "in_invoice", self.vendor, 80)
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 120)
        lines = self._debt_lines(central_bill | yesterday_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()
        self._assert_paid_tab(payment, lines)
        self.assertFalse(payment.matched_move_line_ids.filtered(
            lambda line: line.move_id.kai_origin_payment_id == payment
        ))

    def test_payment_receipt_with_only_central_company_selected(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 70)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 30)
        lines = self._debt_lines(yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()
        user = self.env["res.users"].create({
            "name": "Contable central",
            "login": "contable_central_mb",
            "email": "contable_central_mb@example.com",
            "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
                self.env.ref("account.group_account_user").id,
            ])],
        })
        restricted = payment.with_user(user).with_context(allowed_company_ids=[self.company.id])
        self.assertEqual(set(restricted.matched_move_line_ids.ids), set(lines.ids))
        paid_tab = restricted.web_read({
            "matched_move_line_ids": {"fields": {"id": {}}},
        })
        shown_ids = {row["id"] for row in paid_tab[0]["matched_move_line_ids"]}
        self.assertEqual(shown_ids, set(lines.ids))
        self.assertAlmostEqual(restricted.matched_amount, 100.0, places=2)
        self.assertTrue(restricted.currency_id.is_zero(restricted.unmatched_amount))
        html = self.env["ir.actions.report"].with_user(user).with_context(
            allowed_company_ids=[self.company.id]
        )._render_template(
            "account_payment_pro.report_payment_receipt_document_pro",
            {"o": restricted, "_": _},
        )
        text = html.decode() if isinstance(html, bytes) else str(html)
        self.assertIn(yesterday_bill.name, text)
        self.assertIn(fisa_bill.name, text)

    def test_regularized_payment_shows_the_invoice_once(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._legacy_payment("supplier", self.vendor, lines)
        self._wizard(payment).action_regularize()
        self._assert_paid_tab(payment, lines)
        self.assertFalse(payment.matched_move_line_ids.filtered(
            lambda line: line.move_id.kai_origin_payment_id == payment
        ))
        html = self.env["ir.actions.report"]._render_template(
            "account_payment_pro.report_payment_receipt_document_pro",
            {"o": payment, "_": _},
        )
        text = html.decode() if isinstance(html, bytes) else str(html)
        self.assertIn(bill.name, text)
        self.assertNotIn("Regularización", text)
        self.assertNotIn("On account", text)
        self.assertNotIn("A cuenta", text)

    def _cc_line(self, payment, company):
        lines = payment.move_id.line_ids.filtered(
            lambda line: line.account_id == self.cc_branches and line.kai_settled_company_id == company
        )
        self.assertEqual(len(lines), 1)
        return lines

    def _bridge(self, payment, company):
        bridge = payment.kai_bridge_move_ids.filtered(lambda move: move.company_id == company)
        self.assertEqual(len(bridge), 1)
        self._assert_move_balanced(bridge)
        return bridge

    def _advance_line(self, move, account_type):
        lines = move.line_ids.filtered(
            lambda line: line.account_id.account_type == account_type and not line.kai_debt_move_line_id
        )
        self.assertEqual(len(lines), 1)
        return lines

    def _central_balance(self, bridge):
        lines = bridge.line_ids.filtered(lambda line: line.account_id == self.cc_central)
        return sum(lines.mapped("balance"))

    def test_anticipo_en_una_sucursal(self):
        """El 2436 en chico: factura 100 de Yesterday y pago 150 desde la casa central.

        El residual de la línea de anticipo es el que se concilia con una
        factura posterior de la misma sucursal, el mismo contacto y la misma
        cuenta a pagar.
        """
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        self.assertEqual(payment.kai_advance_company_id, self.yesterday)
        payment.amount = 150
        if payment.move_id and payment.move_id.state == "draft":
            # El borrador se rearma por _kai_synchronize_multi_branch_move, no solo al confirmar.
            draft_cc = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
            self.assertEqual(len(draft_cc), 1)
            self.assertAlmostEqual(draft_cc.balance, 150.0, places=2)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertEqual(payment.move_id.company_id, self.company)
        self._assert_move_balanced(payment.move_id)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))
        cc = self._cc_line(payment, self.yesterday)
        self.assertEqual(cc.partner_id, self.yesterday.partner_id)
        self.assertEqual(cc.kai_settled_company_id, self.yesterday)
        self.assertAlmostEqual(cc.balance, 150.0, places=2)
        bridge = self._bridge(payment, self.yesterday)
        advance = self._advance_line(bridge, "liability_payable")
        self.assertFalse(advance.kai_debt_move_line_id)
        self.assertFalse(advance.reconciled)
        self.assertEqual(advance.partner_id, self.vendor.commercial_partner_id)
        self.assertEqual(advance.account_id, lines.account_id)
        self.assertIn("Anticipo", advance.name)
        self.assertAlmostEqual(advance.balance, 50.0, places=2)
        self.assertAlmostEqual(advance.amount_residual, 50.0, places=2)
        self.assertAlmostEqual(self._central_balance(bridge), -150.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 50.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 150.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -150.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -150.0, places=2)

        payment.action_draft()
        lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(lines.reconciled)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 0.0, places=2)

        payment.action_post()
        self._assert_reconciled(lines)
        bridge = self._bridge(payment, self.yesterday)
        advance = self._advance_line(bridge, "liability_payable")
        self.assertAlmostEqual(advance.amount_residual, 50.0, places=2)
        future = self._invoice(self.yesterday, "in_invoice", self.vendor, 50)
        future_lines = self._debt_lines(future)
        self.assertEqual(advance.account_id, future_lines.account_id)
        self.assertEqual(advance.partner_id, future_lines.partner_id)
        (advance + future_lines).with_company(self.yesterday).reconcile()
        self._assert_reconciled(future_lines)
        self._assert_reconciled(advance)
        payment.invalidate_recordset(["matched_move_line_ids"])
        self.assertIn(future_lines, payment.matched_move_line_ids)
        self.assertIn(lines, payment.matched_move_line_ids)

        payment.action_cancel()
        lines.invalidate_recordset()
        future_lines.invalidate_recordset()
        self.assertEqual(payment.state, "canceled")
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(lines.reconciled)
        self.assertFalse(future_lines.reconciled)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -150.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 0.0, places=2)

    def test_anticipo_con_retenciones(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.payment_method_line_id = self._manual_method_line()
        withholding = self._add_withholding(payment)
        payment.amount = 150.0 - withholding.amount
        payment.action_post()

        self._assert_withholding_on_payer(payment, 150.0)
        self.assertFalse(payment.kai_bridge_move_ids.line_ids.filtered(
            lambda line: line.account_id == self.wth_account
        ))
        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._cc_line(payment, self.yesterday).balance, 150.0, places=2)
        bridge = self._bridge(payment, self.yesterday)
        advance = self._advance_line(bridge, "liability_payable")
        self.assertAlmostEqual(advance.amount_residual, 50.0, places=2)
        self.assertAlmostEqual(self._central_balance(bridge), -150.0, places=2)
        self._assert_move_balanced(payment.move_id)
        self.assertEqual(bridge.company_id, self.yesterday)
        self.assertEqual(payment.move_id.company_id, self.company)

    def test_anticipo_con_cheques_propios(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        today = fields.Date.today()
        self._use_own_checks(payment, [
            (today, 40.0),
            (today + timedelta(days=15), 60.0),
            (today + timedelta(days=45), 50.0),
        ])
        payment.amount = 150.0
        self.assertAlmostEqual(sum(payment.l10n_latam_new_check_ids.mapped("amount")), 150.0, places=2)
        payment.action_post()

        self._assert_check_lines(payment)
        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._cc_line(payment, self.yesterday).balance, 150.0, places=2)
        bridge = self._bridge(payment, self.yesterday)
        advance = self._advance_line(bridge, "liability_payable")
        self.assertAlmostEqual(advance.amount_residual, 50.0, places=2)
        self.assertAlmostEqual(self._central_balance(bridge), -150.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -150.0, places=2)
        self._assert_move_balanced(payment.move_id)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))

    def test_anticipo_sin_sucursal_elegida_no_se_publica(self):
        yesterday = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        payment = self._payment("supplier", self.vendor, self._debt_lines(yesterday | fisa))
        self.assertFalse(payment.kai_advance_company_id)
        payment.amount = 200
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("sucursal del anticipo", str(error.exception).lower())
        self.assertFalse(payment.kai_bridge_move_ids)
        if payment.move_id:
            self.assertNotEqual(payment.move_id.state, "posted")

    def test_anticipo_en_la_sucursal_elegida(self):
        """Deuda Yesterday 100 y Fisa 50, pago 200.

        El anticipo es el bruto menos toda la deuda: 50, y queda en Yesterday.
        La cuenta corriente de Yesterday queda en 150 y la de Fisa en 50. La
        intercompañía suma 200, el bruto del pago.
        """
        yesterday = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        yesterday_lines = self._debt_lines(yesterday)
        fisa_lines = self._debt_lines(fisa)
        payment = self._payment("supplier", self.vendor, yesterday_lines | fisa_lines)
        payment.kai_advance_company_id = self.yesterday
        payment.amount = 200
        self.assertEqual(payment.kai_advance_company_id, self.yesterday)
        payment.action_post()

        self._assert_reconciled(yesterday_lines | fisa_lines)
        self.assertAlmostEqual(self._cc_line(payment, self.yesterday).balance, 150.0, places=2)
        self.assertAlmostEqual(self._cc_line(payment, self.fisa).balance, 50.0, places=2)
        yesterday_bridge = self._bridge(payment, self.yesterday)
        fisa_bridge = self._bridge(payment, self.fisa)
        advance = self._advance_line(yesterday_bridge, "liability_payable")
        self.assertAlmostEqual(advance.balance, 50.0, places=2)
        self.assertAlmostEqual(advance.amount_residual, 50.0, places=2)
        self.assertFalse(advance.reconciled)
        self.assertFalse(fisa_bridge.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable" and not line.kai_debt_move_line_id
        ))
        self.assertAlmostEqual(self._central_balance(yesterday_bridge), -150.0, places=2)
        self.assertAlmostEqual(self._central_balance(fisa_bridge), -50.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 200.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -150.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.fisa), -50.0, places=2)
        self._assert_move_balanced(payment.move_id)

    def test_recibo_de_cliente_con_anticipo(self):
        invoice = self._invoice(self.yesterday, "out_invoice", self.customer, 100)
        lines = self._debt_lines(invoice)
        payment = self._payment("customer", self.customer, lines)
        self.assertEqual(payment.kai_advance_company_id, self.yesterday)
        payment.amount = 150
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ))
        cc = self._cc_line(payment, self.yesterday)
        self.assertAlmostEqual(cc.balance, -150.0, places=2)
        self.assertEqual(cc.partner_id, self.yesterday.partner_id)
        self.assertEqual(cc.kai_settled_company_id, self.yesterday)
        bridge = self._bridge(payment, self.yesterday)
        advance = self._advance_line(bridge, "asset_receivable")
        self.assertEqual(advance.partner_id, self.customer.commercial_partner_id)
        self.assertFalse(advance.kai_debt_move_line_id)
        self.assertFalse(advance.reconciled)
        self.assertAlmostEqual(advance.balance, -50.0, places=2)
        self.assertAlmostEqual(advance.amount_residual, -50.0, places=2)
        self.assertAlmostEqual(advance.credit, 50.0, places=2)
        self.assertAlmostEqual(self._central_balance(bridge), 150.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), -50.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), -150.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 150.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), 150.0, places=2)
        self._assert_move_balanced(payment.move_id)

    def test_anticipo_con_deuda_de_casa_central_y_una_sucursal(self):
        central = self._invoice(self.company, "in_invoice", self.vendor, 80)
        yesterday = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        central_lines = self._debt_lines(central)
        yesterday_lines = self._debt_lines(yesterday)
        payment = self._payment("supplier", self.vendor, central_lines | yesterday_lines)
        self.assertEqual(payment.kai_advance_company_id, self.yesterday)
        payment.amount = 220
        payment.action_post()

        self._assert_reconciled(central_lines | yesterday_lines)
        payable = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        )
        self.assertEqual(len(payable), 1)
        self.assertAlmostEqual(payable.balance, 80.0, places=2)
        self.assertEqual(payable.partner_id, self.vendor.commercial_partner_id)
        central_match = central_lines.matched_debit_ids | central_lines.matched_credit_ids
        counterparts = (central_match.debit_move_id | central_match.credit_move_id) - central_lines
        self.assertTrue(counterparts)
        self.assertTrue(all(line.move_id == payment.move_id for line in counterparts))
        self.assertAlmostEqual(self._cc_line(payment, self.yesterday).balance, 140.0, places=2)
        bridge = self._bridge(payment, self.yesterday)
        advance = self._advance_line(bridge, "liability_payable")
        self.assertAlmostEqual(advance.balance, 40.0, places=2)
        self.assertAlmostEqual(advance.amount_residual, 40.0, places=2)
        self.assertFalse(advance.reconciled)
        self.assertAlmostEqual(self._central_balance(bridge), -140.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 40.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 140.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -140.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -220.0, places=2)
        self.assertEqual(payment.kai_bridge_move_ids.company_id, self.yesterday)
        self._assert_move_balanced(payment.move_id)
