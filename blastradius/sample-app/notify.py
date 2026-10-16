"""Tiny shop notifications (sample app for the BlastRadius demo)."""


def send_email(to: str, subject: str) -> bool:
    """Pretend to send an email. Returns True on 'success'."""
    print(f"[email] to={to} subject={subject}")
    return True


def send_receipt(order: dict) -> bool:
    """Email the order receipt to the buyer."""
    return send_email(order["user"], f"Receipt: total ${order['total']}")
