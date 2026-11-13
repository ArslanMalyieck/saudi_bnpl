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

Inputs: provider statement, inclusive settlement period, bank credit, and fee tax invoice.

1. Record the provider fee Purchase Invoice (Supplier = provider supplier) for fee and VAT.
   - SAR only; fully outstanding; one set of additive VAT rows on the selected VAT account; no advances/write-off/rounding drift.
2. Create BNPL Settlement and select the Provider Account. Company is filled from the permanent provider configuration.
3. Select the POS Profile, inclusive From Date and To Date, statement reference, and payout date.
4. Click Fetch Provider Invoices. The app finds unclaimed submitted provider payment rows, groups multiple matching payment rows per source invoice, and derives signed gross from selected rows. Deselect rows that are not on the provider statement.
5. Enter fee and VAT. Net is derived as gross minus fee minus VAT. Select the submitted fee Purchase Invoice and VAT account when a fee applies.
6. Save, then click Create Draft Journal Entry. The app atomically reserves each selected payment row and creates one draft JE:
   - Dr Bank = Net
   - Dr Supplier Payable = Fee + VAT (with PI reference)
   - Cr Clearing = Gross
7. Finance reviews and submits the JE, then matches the single provider bank receipt in Bank Reconciliation. The app does not create a Payment Entry or submit the JE.

Generated entries use the dedicated `BNPL-JV-.YYYY.-` naming series (for example, `BNPL-JV-2026-00001`). They also carry read-only links to the BNPL Settlement, Provider Account, and POS Profile, the inclusive period, and the provider statement reference. The user remark repeats the provider, statement reference, profile, and inclusive period so the accounting purpose remains visible in Journal Entry and General Ledger drill-downs. These fields are populated only on app-generated entries; ordinary Journal Entries retain their configured naming series and behavior.

Both direct POS-created Sales Invoices and native POS Invoices are supported. Direct invoices must be submitted POS Sales Invoices and consolidated Sales Invoices are excluded. Native POS Invoices are eligible only after linkage to a submitted consolidated Sales Invoice. Amounts come from signed company-currency payment rows, not invoice grand total or outstanding amount, so mixed tenders, partial provider payments, and returns are represented correctly.

### Late Catch-up

Fetching excludes payment rows claimed by another settlement. If an unclaimed invoice posts inside any earlier JE-linked interval for the same provider and POS Profile, it is marked Late Entry and remains selectable for a catch-up settlement. An empty range overlapping an earlier completed interval reports that the period appears already settled with no new invoices. This is period-based detection; document creation and modification timestamps are not treated as exact submission times.

## BNPL Provider Ledger

Accounts Managers can use the standard **BNPL Provider Ledger** Script Report for audit and reconciliation. Company and Provider Account are required; POS Profile, From Date, and To Date are optional. The report combines canonical submitted provider activity from direct POS-created Sales Invoices and consolidated native POS Invoice payment rows with BNPL Settlement activity. It includes unclaimed invoices and links to sources, GL vouchers, settlements, and Journal Entries.

Sales increase the posted provider receivable as debits and returns reduce it as credits. A settlement credit reduces the posted running balance only after its Journal Entry is submitted. A draft JE remains visible as `Draft (Pending)` with its expected credit, but does not change the posted running balance or submitted-settlement summary. When From Date is supplied, prior posted activity is included in the opening calculation but omitted from the displayed rows; the closing posted balance therefore remains cumulative. Report summaries show source debits, return credits, submitted settlement credits, and the closing posted balance for the selected scope.

General Ledger remains the accounting book of record and can be drilled through the linked Journal Entry; the provider report supplies the source-to-settlement audit trail. Current test/demo configurations may share `Cash - BS`, but that does not provide provider-level clearing segregation in General Ledger. Production must use one dedicated provider clearing account per provider so each provider balance can be independently reconciled and audited.

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
- BNPL Provider Ledger: Accounts Manager only

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

Tests validate hashing, signed amount grouping, claims, duplicate prevention, late detection, provider config rules, metadata, and draft creation logic, but do not exercise real GL posting, scheduler, concurrent SQL transactions, or browser flows.

## Limitations (Important)

- No provider API integration or automatic payout detection; finance must match the provider statement and bank receipt
- SAR only; no foreign currency
- Fee invoice must exactly equal fee and VAT; no partial/complex terms
- Deterministic payment-row claims prevent duplicate settlement; finance must still review the draft before submission
- Company rename unsupported (document names are hash-based)

## Uninstall

This app creates no background jobs or external credentials. Remove via bench:
```
bench --site your.site uninstall-app saudi_bnpl
bench --site your.site migrate
```

## Support

Pilot-quality. Review with finance and test in isolation before use. Do not submit draft JEs without independent validation and bank reconciliation.
