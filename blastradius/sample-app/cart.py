"""Tiny shop cart (sample app for the BlastRadius demo)."""

from notify import send_receipt
from pricing import apply_coupon, calc_discount


def add_item(cart: list, item: str) -> list:
    cart.append(item)
    return cart


def checkout(cart: list, user: str, coupon: str = "") -> dict:
    """Price the cart, apply coupon, and email a receipt."""
    total = sum(10.0 for _ in cart)
    total = calc_discount(total, "gold")
    total -= apply_coupon(coupon)
    order = {"user": user, "items": list(cart), "total": max(total, 0.0)}
    send_receipt(order)
    return order
