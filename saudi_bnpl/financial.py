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
        raise ValueError("Gross and net must be positive; fee and VAT cannot be negative. Returns are not supported")
    if not fee and vat:
        raise ValueError("VAT without a provider fee is not supported")
    if gross != net + fee + vat:
        raise ValueError("Gross must equal net payout plus fee plus VAT")
    return gross, fee, vat, net
