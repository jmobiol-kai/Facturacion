# -*- coding: utf-8 -*-

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class MsbSalesDefaultTaxesTestCommon(AccountTestInvoicingCommon):
    """Shared fixtures for msb_default_tax_and_account tests (Odoo 19 CE).

    Subclasses ``AccountTestInvoicingCommon`` so a chart of accounts,
    company, partners and default taxes are available (account.move / order
    tax & account resolution needs them in 19).

    Provides:
    - company, partner
    - taxes: tax_sale_21, tax_sale_10
    - journals:
        - journal_with_taxes: sale journal with default taxes (21%),
          default account set, usar_cuenta_defecto_facturas=True
        - journal_without_taxes: sale journal without default taxes,
          default account set, usar_cuenta_defecto_facturas=False
    - product with product taxes (10%) so it differs from journal (21%)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # AccountTestInvoicingCommon runs as a restricted accounting user, so grant
        # the sales group to let the tests create sale.order records.
        # NOTE (19): res.users.groups_id was renamed to group_ids.
        cls.env.user.sudo().group_ids |= cls.env.ref('sales_team.group_sale_salesman')

        # Controlled tax environment: core _force_default_sale/purchase_tax() Command.links
        # each company's default tax onto products (non-deterministically). Clear all company
        # defaults BEFORE building fixtures so every fixture product carries EXACTLY the tax we
        # assign — a prerequisite for asserting concrete business outcomes rather than tautologies.
        cls.env['res.company'].sudo().search([]).write({
            'account_sale_tax_id': False,
            'account_purchase_tax_id': False,
        })

        # Base company and partner
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'is_company': True,
        })

        # Taxes (sale)
        cls.tax_sale_21 = cls.env['account.tax'].create({
            'name': 'VAT 21%',
            'amount': 21,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        cls.tax_sale_10 = cls.env['account.tax'].create({
            'name': 'VAT 10%',
            'amount': 10,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        # Extra distinct-rate sale taxes used to make swaps financially observable
        # (a same-rate rename proves nothing).
        cls.tax_sale_27 = cls.env['account.tax'].create({
            'name': 'VAT 27%',
            'amount': 27,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        cls.tax_sale_0 = cls.env['account.tax'].create({
            'name': 'VAT 0%',
            'amount': 0,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # Income accounts for journals.  In 19 ``account.account`` no longer
        # has ``company_id`` (B9); the company scope is the M2m ``company_ids``.
        cls.journal_account_1 = cls.env['account.account'].create({
            'name': 'Journal Default Income 1',
            'code': 'JDINC1',
            'account_type': 'income',
            'company_ids': [(6, 0, cls.company.ids)],
        })
        cls.journal_account_2 = cls.env['account.account'].create({
            'name': 'Journal Default Income 2',
            'code': 'JDINC2',
            'account_type': 'income',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        # Journals
        cls.journal_with_taxes = cls.env['account.journal'].create({
            'name': 'Sales Journal With Taxes',
            'type': 'sale',
            'code': 'SJWT',
            'company_id': cls.company.id,
            'default_account_id': cls.journal_account_1.id,
            'usar_cuenta_defecto_facturas': True,
        })

        cls.journal_without_taxes = cls.env['account.journal'].create({
            'name': 'Sales Journal Without Taxes',
            'type': 'sale',
            'code': 'SJNT',
            'company_id': cls.company.id,
            'default_account_id': cls.journal_account_2.id,
            'usar_cuenta_defecto_facturas': False,
        })

        # Product with product taxes (10%) so it differs from journal (21%).
        # In 19 the storable ``type='product'`` is gone (B8) -> 'consu'.
        cls.product = cls.env['product.product'].create({
            'name': 'Test Product',
            'type': 'consu',
            'list_price': 100.0,
            'taxes_id': [(6, 0, [cls.tax_sale_10.id])],
        })
        # AccountTestInvoicingCommon's company ships a default sale tax that
        # leaks into taxes_id on create. Pin it to exactly tax_sale_10 so the
        # cross-company default doesn't pollute the expectations.
        cls.product.taxes_id = cls.tax_sale_10

    def create_tax_mapping(self, journal, original_tax, replacement_tax, tax_type='sale'):
        """Helper method to create tax mappings"""
        return self.env['account.journal.tax.mapping'].create({
            'journal_id': journal.id,
            'original_tax_id': original_tax.id,
            'replacement_tax_id': replacement_tax.id,
            'tax_type': tax_type,
        })


class MsbPurchaseDefaultTaxesTestCommon(AccountTestInvoicingCommon):
    """Shared fixtures for purchase-related tests"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Grant the purchase group (restricted accounting user otherwise). (19: group_ids)
        cls.env.user.sudo().group_ids |= cls.env.ref('purchase.group_purchase_user')

        # Controlled tax environment (see MsbSalesDefaultTaxesTestCommon): clear all company
        # default taxes so fixture products carry exactly the taxes we assign.
        cls.env['res.company'].sudo().search([]).write({
            'account_sale_tax_id': False,
            'account_purchase_tax_id': False,
        })

        # Base company and partner
        cls.company = cls.env.company
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Test Vendor',
            'is_company': True,
            'supplier_rank': 1,
        })

        # Taxes (purchase)
        cls.tax_purchase_21 = cls.env['account.tax'].create({
            'name': 'Purchase VAT 21%',
            'amount': 21,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })
        cls.tax_purchase_10 = cls.env['account.tax'].create({
            'name': 'Purchase VAT 10%',
            'amount': 10,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })

        # Expense accounts for journals (B9: company_ids, not company_id).
        cls.purchase_journal_account_1 = cls.env['account.account'].create({
            'name': 'Purchase Journal Default Expense 1',
            'code': 'PJDEXP1',
            'account_type': 'expense',
            'company_ids': [(6, 0, cls.company.ids)],
        })
        cls.purchase_journal_account_2 = cls.env['account.account'].create({
            'name': 'Purchase Journal Default Expense 2',
            'code': 'PJDEXP2',
            'account_type': 'expense',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        # Purchase journals
        cls.purchase_journal_with_taxes = cls.env['account.journal'].create({
            'name': 'Purchase Journal With Taxes',
            'type': 'purchase',
            'code': 'PJWT',
            'company_id': cls.company.id,
            'default_account_id': cls.purchase_journal_account_1.id,
            'usar_cuenta_defecto_compras': True,
        })

        cls.purchase_journal_without_taxes = cls.env['account.journal'].create({
            'name': 'Purchase Journal Without Taxes',
            'type': 'purchase',
            'code': 'PJNT',
            'company_id': cls.company.id,
            'default_account_id': cls.purchase_journal_account_2.id,
            'usar_cuenta_defecto_compras': False,
        })

        # Product with supplier taxes (10%) so it differs from potential
        # mappings (21%).  B8: 'product' -> 'consu' (+ is_storable where
        # storability matters; not needed here).
        cls.purchase_product = cls.env['product.product'].create({
            'name': 'Test Purchase Product',
            'type': 'consu',
            'list_price': 100.0,
            # Bill on ORDERED quantities so a PO -> vendor-bill flow invoices qty=1
            # without needing a stock receipt (default 'receive' would yield qty 0).
            'purchase_method': 'purchase',
            'supplier_taxes_id': [(6, 0, [cls.tax_purchase_10.id])],
        })
        # Pin supplier taxes to exactly tax_purchase_10 (company default leaks in on create).
        cls.purchase_product.supplier_taxes_id = cls.tax_purchase_10

    def create_tax_mapping(self, journal, original_tax, replacement_tax, tax_type='purchase'):
        """Helper method to create tax mappings for purchases"""
        return self.env['account.journal.tax.mapping'].create({
            'journal_id': journal.id,
            'original_tax_id': original_tax.id,
            'replacement_tax_id': replacement_tax.id,
            'tax_type': tax_type,
        })
