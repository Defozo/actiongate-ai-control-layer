from datetime import timedelta
from sqlalchemy import select, text
from .db import DataObject, transaction, now
from .security import encrypt

SUPPLIERS = {
    "acme": {"name": "Northstar Components", "country": "Poland", "service": "Industrial components", "status": "Reviewed"},
    "globex": {"name": "Harbor Logistics", "country": "Denmark", "service": "Transport", "status": "Reviewed"},
    "synthetic_test_tenant": {"name": "Synthetic Supply", "country": "Poland", "service": "Test fixtures", "status": "Demo"},
}


def seed():
    with transaction() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(871222)"))
        for tenant, supplier in SUPPLIERS.items():
            name = f"supplier-{tenant}-1"
            if db.scalar(select(DataObject).where(DataObject.tenant == tenant, DataObject.name == name)):
                continue
            content = (f"Supplier review: {supplier['name']}. Delivery reliability: 97 percent. "
                       "Insurance is current. Two late deliveries were corrected in September. "
                       "Recommended action: approve with quarterly delivery monitoring. "
                       "This internal assessment is confidential.")
            db.add(DataObject(tenant=tenant, name=name, kind="document", owner="trusted-registry",
                encrypted=encrypt(content), label=2, origins=["supplier_document"],
                expires_at=now()+timedelta(days=365)))
