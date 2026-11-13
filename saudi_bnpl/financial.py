"""Pure statement arithmetic and stable names, independent of a Frappe site."""

import hashlib
import json
import unicodedata
from decimal import Decimal, InvalidOperation


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def stable_name(prefix, *parts):
    normalized = [normalize(part) for part in parts]
    if any(not part for part in normalized):
        raise ValueError("Company, provider and statement reference must not be blank")
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def money(value):
    try:
        amount = Decimal(str(value if value is not None else 0))
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError("Amounts must be finite SAR values with at most two decimals") from None
    return amount


def validate_amounts(gross, fee, vat, net):
    gross, fee, vat, net = map(money, (gross, fee, vat, net))
    if gross <= 0 or net <= 0 or fee < 0 or vat < 0:
        raise ValueError("Gross and net must be positive after signed returns; fee and VAT cannot be negative")
    if not fee and vat:
        raise ValueError("VAT without a provider fee is not supported")
    if gross != net + fee + vat:
        raise ValueError("Gross must equal net payout plus fee plus VAT")
    return gross, fee, vat, net


def payment_row_ids(value):
    """Return a stable, duplicate-free tuple of payment child row names."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Payment row IDs must be a JSON list") from None
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("At least one payment row ID is required")
    rows = tuple(sorted(str(row).strip() for row in value))
    if any(not row for row in rows) or len(rows) != len(set(rows)):
        raise ValueError("Payment row IDs must be non-blank and unique")
    return rows


def payment_claim_name(payment_row):
    row = payment_row_ids([payment_row])[0]
    return "BNPL-C-" + hashlib.sha256(row.encode("utf-8")).hexdigest()


def selection_hash(rows):
    selected = []
    for row in rows:
        get = row.get if hasattr(row, "get") else lambda key: getattr(row, key, None)
        if get("selected"):
            selected.extend(payment_row_ids(get("payment_row_ids")))
    payload = json.dumps(sorted(selected), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def selected_amount(rows):
    total = Decimal("0.00")
    for row in rows:
        get = row.get if hasattr(row, "get") else lambda key: getattr(row, key, None)
        if get("selected"):
            total += money(get("provider_amount"))
    return total
