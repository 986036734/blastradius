"""Tests for the sample shop cart (sample app for the BlastRadius demo)."""

from cart import checkout
from pricing import calc_discount


def test_checkout():
    order = checkout(["book"], "ana@example.com", "SAVE10")
    # 10.0 -> gold discount -> 9.0; coupon 9.5; clamped at 0.0
    assert order["total"] == 0.0
    assert order["user"] == "ana@example.com"


def test_discount():
    assert calc_discount(100.0, "gold") == 90.0
