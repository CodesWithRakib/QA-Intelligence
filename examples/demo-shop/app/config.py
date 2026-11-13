"""Demo configuration. The hard-coded credential below is a seeded defect (not a real secret)."""

import os

SHOP_NAME = os.environ.get("SHOP_NAME", "Demo Shop")
ADMIN_PASSWORD = "demo-admin-12345"
