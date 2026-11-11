"""The canonical category vocabulary and conservative natural-language aliases."""
from __future__ import annotations

import re

CATEGORIES = [
    'Housing / Rent', 'Food / Dining', 'Groceries', 'Transport', 'Shopping',
    'Bills', 'Family support', 'Entertainment', 'Subscriptions', 'Healthcare',
    'Education', 'Travel', 'Personal Care', 'Investments', 'Savings', 'Income',
    'Transfers', 'Loans', 'Gifts', 'Other', 'Uncategorized',
]

# Transport and Travel remain distinct persisted categories. Local ground
# travel maps to Transport; rail, air, lodging and trip costs map to Travel.
CATEGORY_ALIASES = {
    'food': 'Food / Dining', 'dining': 'Food / Dining', 'restaurant': 'Food / Dining', 'restaurants': 'Food / Dining',
    'grocery': 'Groceries', 'groceries': 'Groceries', 'supermarket': 'Groceries',
    'shopping': 'Shopping', 'clothes': 'Shopping', 'electronics': 'Shopping',
    'transport': 'Transport', 'transportation': 'Transport', 'cab': 'Transport', 'taxi': 'Transport',
    'auto': 'Transport', 'metro': 'Transport', 'bus': 'Transport',
    'travel': 'Travel', 'train': 'Travel', 'railway': 'Travel', 'flight': 'Travel',
    'education': 'Education', 'college': 'Education', 'tuition': 'Education', 'course': 'Education', 'courses': 'Education',
    'rent': 'Housing / Rent', 'housing': 'Housing / Rent',
    'bills': 'Bills', 'bills / utilities': 'Bills', 'utilities': 'Bills', 'bill': 'Bills', 'electricity': 'Bills', 'internet': 'Bills', 'mobile recharge': 'Bills', 'recharge': 'Bills',
    'healthcare': 'Healthcare', 'pharmacy': 'Healthcare', 'hospital': 'Healthcare',
    'entertainment': 'Entertainment', 'movie': 'Entertainment', 'movies': 'Entertainment',
    'subscription': 'Subscriptions', 'subscriptions': 'Subscriptions',
    'personal care': 'Personal Care', 'salon': 'Personal Care',
    # Historical labels retained in imported/demo rows. They are aliases for
    # filtering and analytics; existing stored values are never rewritten.
    'internal transfer': 'Transfers', 'salary': 'Income',
}


def normalize_category(value: str | None) -> str | None:
    if value is None:
        return None
    text = ' '.join(str(value).strip().split())
    if not text:
        return None
    for category in CATEGORIES:
        if text.casefold() == category.casefold():
            return category
    return CATEGORY_ALIASES.get(text.casefold())


def category_from_text(value: str | None) -> str | None:
    text = str(value or '').casefold()
    for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
        if re.search(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', text):
            return CATEGORY_ALIASES[alias]
    return None


def category_matches(stored_value: str | None, canonical: str | None) -> bool:
    """Match canonical filters against current and known historical labels."""
    if canonical is None:
        return True
    return normalize_category(stored_value) == canonical or stored_value == canonical
