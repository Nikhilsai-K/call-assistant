import csv
import io

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.auth import Principal, current_principal
from app.db.session import get_session
from app.models import DncEntry
from app.services.compliance import parse_number

router = APIRouter(prefix="/dnc", tags=["dnc"])


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_dnc(
    file: UploadFile = File(...), p: Principal = Depends(current_principal)
) -> dict:
    raw = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    rows: list[dict] = []
    skipped = 0
    for row in reader:
        num = (row.get("phone") or row.get("e164") or "").strip()
        if not num:
            continue
        try:
            info = parse_number(num)
        except Exception:
            skipped += 1
            continue
        rows.append({"phone": info.e164, "source": row.get("source", "csv_import")})

    if not rows:
        return {"inserted": 0, "skipped": skipped}

    async with get_session(p.org_id) as s:
        stmt = pg_insert(DncEntry).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["phone"])
        await s.execute(stmt)
    return {"inserted": len(rows), "skipped": skipped}
