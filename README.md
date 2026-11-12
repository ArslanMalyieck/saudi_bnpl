# Saudi BNPL (Pilot)

Manual settlement workflow for Tabby, Tamara, Emkan, NEO, and Madfu on ERPNext v16. This app is not a payment integration. It helps finance book BNPL provider statements safely using a draft Journal Entry, with a controlled setup and clear responsibilities.

Status: pilot-quality. Test on an isolated copy before production use.

## Compatibility

- Frappe/ERPNext: v16
- Currency: SAR only (company, accounts, invoices)
- Multi-company supported in configuration; permissions must be reviewed

## Install

1. Get the app into your bench:
```
bench get-app https://github.com/ArslanMalyieck/saudi_bnpl.git
```
2. Install on a site:
```
bench --site your.site install-app saudi_bnpl
bench --site your.site migrate
```

## Configure

Create one BNPL Provider Account per company/provider pair.

- Provider Name: Tabby, Tamara, Emkan, NEO, Madfu
- Company: target company
- Mode of Payment: existing MoP configured to post to the clearing account for that company
- Clearing Account: Asset/Balance Sheet account with account_type = Cash
- Payout Bank Account: Bank account receiving provider deposits
- Supplier: the BNPL provider as Supplier master

Notes
- Do not change provider configs once used. You can disable/enable.
- Ensure the Mode of Payment maps to the clearing account for this company (standard ERPNext setting).
- The clearing account being classified as Cash can affect cash reports; finance must approve.

## POS Sale Flow (at invoice time)

Goal: customer balance zero at sale time; clearing accumulates provider gross.

1. POS user selects Mode of Payment = BNPL <Provider> on the Sales Invoice.
2. ERPNext GL on submit:
   - Dr Clearing (BNPL MoP default account)
   - Cr Income and Cr Output VAT (standard)
   - Debtors cleared by POS payment row
3. POS returns must use the same provider MoP to reverse the clearing as needed.

## Settlement Flow (at payout time)

Inputs: provider statement (gross, fee, VAT, net), bank credit, fee tax invoice.

1. Record the provider fee Purchase Invoice (Supplier = provider supplier) for fee and VAT.
   - SAR only; fully outstanding; one set of additive VAT rows on the selected VAT account; no advances/write-off/rounding drift.
2. Create BNPL Settlement with:
   - Provider Account, Company
   - Statement Reference (unique per provider/company)
   - Payout Date
   - Gross, Fee, VAT, Net (SAR, 2 decimals; gross = net + fee + VAT)
   - Fee Purchase Invoice and VAT Account (if fee > 0)
3. Click Create Draft Journal Entry. The app creates draft JE:
   - Dr Bank = Net
   - Dr Supplier Payable = Fee + VAT (with PI reference)
   - Cr Clearing = Gross
4. Finance reviews and submits the JE, then matches bank credit in Bank Reconciliation.

### Example

POS sale with VAT 15%:
- Gross (incl VAT): 115.0
- Fee: 10.0
- VAT on fee: 1.5
- Net payout: 103.5

Draft JE:
- Dr Bank 103.5
- Dr Supplier Payable 11.5 (ref Purchase Invoice)
- Cr Clearing 115.0

## Permissions

- BNPL Provider Account and BNPL Settlement: Accounts Manager (read/write/create)
- Journal Entry creation requires user to have JE create permission

## Demo And Tests

CLI demo helpers (do not use in production):

```
# Quick Tamara POS scenario for a company (creates item, customer, fee PI, settlement, draft JE)
bench --site your.site execute saudi_bnpl.scripts.demo_setup.pos_via_provider_scenario --kwargs "{'company':'BOT Solutions','provider':'Tamara'}"

# Create provider config only
bench --site your.site execute saudi_bnpl.scripts.demo_setup.create_provider --kwargs "{'company':'BOT Solutions','provider':'Tabby','clearing_account':'Cash - BS','bank_account':'Bank - BS'}"

# Create fee PI only
bench --site your.site execute saudi_bnpl.scripts.demo_setup.create_fee_invoice --kwargs "{'company':'BOT Solutions','provider':'Tabby','vat_account':'Output VAT - BS'}"
```

Unit tests (pure/mocked):
```
cd apps/saudi_bnpl
PYTHONDONTWRITEBYTECODE=1 /path/to/bench/env/bin/python -m unittest discover -s tests -v
```

Tests validate hashing, amounts, provider config rules, and draft creation logic, but do not exercise real GL posting, scheduler, SQL lock behavior, or browser flows.

## Limitations (Important)

- No provider API integration, payout detection, invoice matching, or refunds handling
- SAR only; no foreign currency
- Fee invoice must exactly equal fee and VAT; no partial/complex terms
- Concurrency protection is best-effort; finance must review before submission
- Company rename unsupported (document names are hash-based)

## Uninstall

This app creates no background jobs or external credentials. Remove via bench:
```
bench --site your.site uninstall-app saudi_bnpl
bench --site your.site migrate
```

## Support

Pilot-quality. Review with finance and test in isolation before use. Do not submit draft JEs without independent validation and bank reconciliation.
