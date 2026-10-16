"""Tiny shop pricing helpers (sample app for the BlastRadius demo)."""


def calc_discount(price: float, tier: str) -> float:
    """Return the price after the tier discount. Tiers: basic/silver/gold."""
    rates = {"basic": 0.0, "silver": 0.05, "gold": 0.10}
    rate = rates.get(tier, 0.0)
    return round(price * (1 - rate), 2)


def apply_coupon(code: str) -> float:
    """Return the flat coupon value. SAVE10 also stacks a silver discount."""
    if code == "SAVE10":
        return calc_discount(10.0, "silver")
    return 0.0
