from __future__ import annotations

import re
from datetime import datetime


def valid_npi(npi: str) -> bool:
    if not npi or not re.fullmatch(r"\d{10}", npi):
        return False
    digits = [int(c) for c in "80840" + npi[:9]]   # NPI Luhn with the 80840 prefix
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total + int(npi[9])) % 10 == 0

def normalize_dob(dob: str) -> str:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(dob, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError("unrecognized DOB format")
