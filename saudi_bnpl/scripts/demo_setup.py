import datetime as _dt

import frappe


def _first(name, filters=None, order_by=None):
    rows = frappe.get_all(name, filters=filters or {}, order_by=order_by or "modified desc", limit=1)
    return rows[0]["name"] if rows else None


def _ensure_item(code: str):
    if frappe.db.exists("Item", code):
        return code
    doc = frappe.get_doc({
        "doctype": "Item",
        "item_code": code,
        "item_name": code,
        "item_group": _first("Item Group", {"is_group": 0}) or "All Item Groups",
        "is_stock_item": 0,
        "include_item_in_manufacturing": 0,
        "maintain_stock": 0,
    })
    doc.insert()
    return doc.name


def _find_account(company: str, account_type: str):
    return _first(
        "Account",
        filters={"company": company, "account_type": account_type, "is_group": 0},
        order_by="name asc",
    )


def _ensure_mop(name: str, company: str, default_account: str):
    if frappe.db.exists("Mode of Payment", name):
        # ensure the company default account row exists
        mop = frappe.get_doc("Mode of Payment", name)
        if not any(row.company == company for row in (mop.accounts or [])):
            mop.append("accounts", {"company": company, "default_account": default_account})
            mop.save()
        return name
    mop = frappe.get_doc({
        "doctype": "Mode of Payment",
        "mode_of_payment": name,
        "type": "Bank",
        "enabled": 1,
        "accounts": [{"company": company, "default_account": default_account}],
    })
    mop.insert()
    return mop.name


def _ensure_supplier(name: str):
    if frappe.db.exists("Supplier", name):
        return name
    group = _first("Supplier Group", {"is_group": 0}) or "All Supplier Groups"
    supp = frappe.get_doc({
        "doctype": "Supplier",
        "supplier_name": name,
        "supplier_group": group,
        "supplier_type": "Company",
    })
    supp.insert()
    return supp.name


def run(provider: str = "Tabby", company: str | None = None, *,
        clearing_account: str | None = None, bank_account: str | None = None, vat_account: str | None = None):
    """Create a minimal BNPL demo: provider config, fee PI, statement, and draft JE.

    Preconditions: an ERPNext company with a Cash account (clearing), a Bank account (payout)
    and at least one VAT Tax account exist. Uses SAR. Designed for a local test site.
    """
    frappe.only_for(("System Manager", "Accounts Manager"))

    company = company or _first("Company")
    if not company:
        raise RuntimeError("No Company found. Create a Company first.")

    # Accounts
    clearing = clearing_account or _find_account(company, "Cash")
    bank = bank_account or _find_account(company, "Bank")
    vat_account = vat_account or _first("Account", {"company": company, "account_type": "Tax", "is_group": 0})
    if not clearing or not bank:
        raise RuntimeError("Could not find required accounts: Cash (clearing) and Bank (payout).")
    if not vat_account:
        raise RuntimeError("Could not find a leaf Tax account for VAT. Configure a VAT account and retry.")

    # Mode of Payment and Supplier
    mop = _ensure_mop(f"BNPL {provider}", company, clearing)
    supplier = _ensure_supplier(f"BNPL {provider} Supplier")

    # Provider config
    pconf = frappe.db.exists("BNPL Provider Account", {"provider_name": provider, "company": company})
    if not pconf:
        doc = frappe.get_doc({
            "doctype": "BNPL Provider Account",
            "provider_name": provider,
            "company": company,
            "enabled": 1,
            "mode_of_payment": mop,
            "clearing_account": clearing,
            "bank_account": bank,
            "supplier": supplier,
        })
        doc.insert()
        pconf = doc.name

    # Fee Purchase Invoice (50 + 7.5 VAT)
    item = _ensure_item("BNPL Fee")
    today = _dt.date.today()
    pi = frappe.get_doc({
        "doctype": "Purchase Invoice",
        "company": company,
        "supplier": supplier,
        "posting_date": today,
        "currency": "SAR",
        "description": "BNPL Fee",
        "bill_no": f"{provider.upper()}-TEST-001",
        "items": [{"item_code": item, "qty": 1, "rate": 50.0, "description": "BNPL Fee"}],
        "taxes": [{
            "charge_type": "On Net Total",
            "add_deduct_tax": "Add",
            "category": "Total",
            "account_head": vat_account,
            "description": "VAT 15%",
            "rate": 15.0,
        }],
    })
    pi.insert()
    pi.submit()

    # Statement: 1000 gross, 50 fee, 7.5 VAT, 942.5 net
    stmt = frappe.get_doc({
        "doctype": "BNPL Settlement",
        "provider_account": pconf,
        "company": company,
        "statement_reference": f"{provider.upper()}-TEST-001",
        "status": "Open",
        "payout_date": today,
        "gross": 1000.0,
        "fee": 50.0,
        "vat": 7.5,
        "net": 942.5,
        "tax_invoice": pi.name,
        "vat_account": vat_account,
    })
    stmt.insert()

    # Generate draft Journal Entry
    from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement import create_journal_entry

    je = create_journal_entry(stmt.name)

    return {
        "company": company,
        "provider_config": pconf,
        "supplier": supplier,
        "mode_of_payment": mop,
        "clearing_account": clearing,
        "bank_account": bank,
        "vat_account": vat_account,
        "purchase_invoice": pi.name,
        "settlement": stmt.name,
        "journal_entry": je,
    }


def run_quick():
    """Wrapper that auto-picks the first company and matching accounts via direct lookups.

    Avoids CLI kwargs quoting issues when invoking from `bench execute`.
    """
    companies = [row["name"] for row in frappe.get_all("Company", fields=["name"], order_by="modified desc")]
    if not companies:
        raise RuntimeError("No Company found. Create a Company first.")
    company = None
    cash = bank = vat = None
    for comp in companies:
        c = frappe.db.get_value("Account", {"company": comp, "is_group": 0, "account_type": "Cash"}, "name")
        b = frappe.db.get_value("Account", {"company": comp, "is_group": 0, "account_type": "Bank"}, "name")
        t = frappe.db.get_value("Account", {"company": comp, "is_group": 0, "account_type": "Tax"}, "name")
        if c and b and t:
            company, cash, bank, vat = comp, c, b, t
            break
    if not (company and cash and bank and vat):
        raise RuntimeError("Could not find any Company with Cash, Bank and Tax accounts. Configure COA and retry.")
    return run(provider="Tabby", company=company, clearing_account=cash, bank_account=bank, vat_account=vat)


def run_for_company(company: str):
    """Run demo for the specified company picking first matching accounts within it."""
    cash = frappe.db.get_value("Account", {"company": company, "is_group": 0, "account_type": "Cash"}, "name")
    bank = frappe.db.get_value("Account", {"company": company, "is_group": 0, "account_type": "Bank"}, "name")
    vat = frappe.db.get_value("Account", {"company": company, "is_group": 0, "account_type": "Tax"}, "name")
    if not (cash and bank and vat):
        raise RuntimeError(f"Required accounts not found in {company} (Cash, Bank, Tax)")
    return run(provider="Tabby", company=company, clearing_account=cash, bank_account=bank, vat_account=vat)


def create_provider(company: str, provider: str, clearing_account: str, bank_account: str):
    mop = _ensure_mop(f"BNPL {provider}", company, clearing_account)
    supplier = _ensure_supplier(f"BNPL {provider} Supplier")
    pconf = frappe.db.exists("BNPL Provider Account", {"provider_name": provider, "company": company})
    if not pconf:
        doc = frappe.get_doc({
            "doctype": "BNPL Provider Account",
            "provider_name": provider,
            "company": company,
            "enabled": 1,
            "mode_of_payment": mop,
            "clearing_account": clearing_account,
            "bank_account": bank_account,
            "supplier": supplier,
        })
        doc.insert()
        pconf = doc.name
    return {"provider_config": pconf, "mode_of_payment": mop, "supplier": supplier}


def create_fee_invoice(company: str, provider: str, vat_account: str, bill_suffix: str = "001", fee: float = 50.0):
    supplier = _ensure_supplier(f"BNPL {provider} Supplier")
    item = _ensure_item("BNPL Fee")
    today = _dt.date.today()
    pi = frappe.get_doc({
        "doctype": "Purchase Invoice",
        "company": company,
        "supplier": supplier,
        "posting_date": today,
        "currency": "SAR",
        "description": "BNPL Fee",
        "disable_rounded_total": 1,
        "bill_no": f"{provider.upper()}-TEST-{bill_suffix}",
        "items": [{"item_code": item, "qty": 1, "rate": float(fee), "description": "BNPL Fee"}],
        "taxes": [{
            "charge_type": "On Net Total",
            "add_deduct_tax": "Add",
            "category": "Total",
            "account_head": vat_account,
            "description": "VAT 15%",
            "rate": 15.0,
        }],
    })
    pi.insert()
    pi.submit()
    return pi.name


def probe_invoice(name: str):
    inv = frappe.get_doc("Purchase Invoice", name)
    row = {
        "name": inv.name,
        "grand_total": float(inv.grand_total),
        "base_grand_total": float(inv.base_grand_total),
        "net_total": float(inv.net_total),
        "outstanding_amount": float(inv.outstanding_amount),
        "total_advance": float(inv.total_advance or 0),
        "rounding_adjustment": float(inv.rounding_adjustment or 0),
        "write_off_amount": float(inv.write_off_amount or 0),
        "payment_schedule_len": len(inv.payment_schedule or []),
        "is_paid": int(inv.is_paid or 0),
    }
    taxes = [
        {
            "account_head": t.account_head,
            "rate": float(t.rate or 0),
            "amount": float(t.tax_amount_after_discount_amount or 0),
            "base_amount": float(t.base_tax_amount_after_discount_amount or 0),
        }
        for t in inv.taxes or []
    ]
    row["taxes"] = taxes
    return row


def create_settlement(company: str, provider_config: str, tax_invoice: str, vat_account: str):
    today = _dt.date.today()
    stmt = frappe.get_doc({
        "doctype": "BNPL Settlement",
        "provider_account": provider_config,
        "company": company,
        "statement_reference": "TABBY-TEST-001",
        "status": "Open",
        "payout_date": today,
        "gross": 1000.0,
        "fee": 50.0,
        "vat": 7.5,
        "net": 942.5,
        "tax_invoice": tax_invoice,
        "vat_account": vat_account,
    })
    stmt.insert()
    from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement import create_journal_entry
    je = create_journal_entry(stmt.name)
    return {"settlement": stmt.name, "journal_entry": je}


def probe_journal(name: str):
    je = frappe.get_doc("Journal Entry", name)
    lines = [
        {
            "account": a.account,
            "debit": float(a.debit_in_account_currency or 0),
            "credit": float(a.credit_in_account_currency or 0),
            "party": a.party,
            "reference_type": a.reference_type,
            "reference_name": a.reference_name,
        }
        for a in je.accounts or []
    ]
    return {"company": je.company, "posting_date": str(je.posting_date), "user_remark": je.user_remark, "accounts": lines}


def _find_income_account(company: str):
    # Try explicit Income account_type, then fallback to any leaf credit account under P&L
    name = _first("Account", {"company": company, "is_group": 0, "account_type": "Income Account"})
    if name:
        return name
    return _first("Account", {"company": company, "is_group": 0, "root_type": "Income"})


def _find_cost_center(company: str):
    return _first("Cost Center", {"company": company, "is_group": 0})


def pos_via_provider_scenario(company: str, provider: str = "Tamara"):
    """Create a POS-style Sales Invoice paid via BNPL provider, then receive payout via settlement.

    Flow:
    - Ensure Mode of Payment and Provider Account for the provider
    - Create a simple item and customer
    - Create Sales Invoice with is_pos=1 and a payment row using provider's MoP
    - Create provider fee Purchase Invoice (10 + 1.5 VAT)
    - Create BNPL Settlement (gross from SI grand_total) and generate Draft JE
    Returns identifiers and the resulting JE account splits.
    """
    cash = _find_account(company, "Cash")
    bank = _find_account(company, "Bank")
    vat_account = _first("Account", {"company": company, "account_type": "Tax", "is_group": 0})
    if not (cash and bank and vat_account):
        raise RuntimeError("Required accounts not found (Cash, Bank, Tax)")

    # Ensure MoP and Provider config
    conf = create_provider(company, provider, cash, bank)
    mop = conf["mode_of_payment"]
    supplier = conf["supplier"]

    # Ensure item and customer
    item = _ensure_item("POS Test Item")
    if not frappe.db.exists("Customer", "BNPL Demo Customer"):
        cust = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": "BNPL Demo Customer",
            "customer_group": _first("Customer Group", {"is_group": 0}) or "All Customer Groups",
            "territory": _first("Territory", {"is_group": 0}) or "All Territories",
        })
        cust.insert()

    income = _find_income_account(company)
    cost_center = _find_cost_center(company)
    if not (income and cost_center):
        raise RuntimeError("Income account or Cost Center not found. Configure COA/Cost Centers and retry.")

    # Pick a sales tax template (if available) to satisfy KSA compliance
    tax_tmpl = _first("Sales Taxes and Charges Template", {"company": company, "disabled": 0})

    # Create POS Sales Invoice: qty 1 x 100, paid via provider MoP
    si = frappe.get_doc({
        "doctype": "Sales Invoice",
        "company": company,
        "customer": "BNPL Demo Customer",
        "is_pos": 1,
        "disable_rounded_total": 1,
        "items": [{
            "item_code": item,
            "qty": 1,
            "rate": 100.0,
            "income_account": income,
            "cost_center": cost_center,
        }],
        **({"taxes_and_charges": tax_tmpl} if tax_tmpl else {}),
        "payments": [{
            "mode_of_payment": mop,
            "amount": 100.0,
            "type": "Receive",
        }],
    })
    si.insert()
    si.submit()

    # Provider fee PI 10 + 1.5 VAT
    pi = create_fee_invoice(company, provider, vat_account, bill_suffix="POS001", fee=10.0)

    # Settlement: gross from SI, fee 10, VAT 1.5, net = grand_total - fee - VAT
    si_doc = frappe.get_doc("Sales Invoice", si.name)
    gross = float(si_doc.grand_total)
    fee = 10.0
    # read VAT from PI to avoid template drift
    pi_doc = frappe.get_doc("Purchase Invoice", pi)
    vat = float(pi_doc.total_taxes_and_charges or 0)
    net = round(gross - fee - vat, 2)

    provider_config = conf["provider_config"]
    # Create settlement matching the POS sale
    new_stmt = frappe.get_doc({
        "doctype": "BNPL Settlement",
        "provider_account": provider_config,
        "company": company,
        "statement_reference": f"{provider.upper()}-POS-001",
        "status": "Open",
        "payout_date": _dt.date.today(),
        "gross": gross,
        "fee": fee,
        "vat": vat,
        "net": net,
        "tax_invoice": pi,
        "vat_account": vat_account,
    })
    new_stmt.insert()
    from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement import create_journal_entry as _cje
    je2 = _cje(new_stmt.name)
    res = {"settlement": new_stmt.name, "journal_entry": je2}
    je_info = probe_journal(res["journal_entry"])  # returns account splits
    return {
        "sales_invoice": si.name,
        "purchase_invoice": pi,
        "settlement": res["settlement"],
        "journal_entry": res["journal_entry"],
        "journal": je_info,
    }


def probe_accounts():
    """Return available accounts for debugging selection logic."""
    company = frappe.db.get_value("Company", {}, "name")
    sql = frappe.db.sql(
        """
        select name, account_type, is_group from `tabAccount`
        where company=%s and is_group=0 and account_type in ('Cash','Bank','Tax')
        order by account_type, name
        """,
        company,
        as_dict=True,
    )
    via_api = {
        "cash": _find_account(company, "Cash"),
        "bank": _find_account(company, "Bank"),
        "tax": _first("Account", {"company": company, "account_type": "Tax", "is_group": 0}),
    }
    return {"company": company, "sql": sql, "via_api": via_api}


def probe_pi_requirements():
    meta = frappe.get_meta("Purchase Invoice")
    fields = [{
        "fieldname": df.fieldname,
        "label": df.label,
        "reqd": df.reqd,
        "in_list_view": df.in_list_view,
    } for df in meta.fields if df.reqd]
    has_top_description = any(df.fieldname == "description" for df in meta.fields)
    item_meta = frappe.get_meta("Purchase Invoice Item")
    item_reqd = [df.fieldname for df in item_meta.fields if df.reqd]
    tax_meta = frappe.get_meta("Purchase Taxes and Charges")
    tax_reqd = [df.fieldname for df in tax_meta.fields if df.reqd]
    return {"pi_required": fields, "pi_has_description": has_top_description, "pi_item_required": item_reqd, "pi_tax_required": tax_reqd}


def probe_latest_fee_invoice():
    supplier = frappe.db.get_value("Supplier", {"supplier_name": ["like", "BNPL % Supplier"]}, "name")
    if not supplier:
        return {"error": "no supplier"}
    rows = frappe.get_all(
        "Purchase Invoice",
        filters={"supplier": supplier, "docstatus": 1},
        fields=["name", "company", "supplier", "posting_date", "currency", "net_total", "base_net_total", "grand_total", "base_grand_total", "outstanding_amount", "total_taxes_and_charges", "base_total_taxes_and_charges", "conversion_rate", "credit_to"],
        order_by="modified desc",
        limit=1,
    )
    if not rows:
        return {"error": "no invoice"}
    name = rows[0]["name"]
    taxes = frappe.get_all("Purchase Taxes and Charges", filters={"parent": name}, fields=["account_head", "category", "add_deduct_tax", "rate", "tax_amount_after_discount_amount", "base_tax_amount_after_discount_amount"])
    return {"invoice": rows[0], "taxes": taxes}
