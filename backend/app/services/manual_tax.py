"""Explicit, user-selected tax rates; this is not an automatic tax classifier."""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.models.invoice_fields import LineItem
from app.models.tax_assignment import TaxAssignment

SOURCES = {
    "PPH21": "https://pajak.go.id/id/artikel/serba-serbi-ter-pph-pasal-21-pajak-baru-atau-formula-baru",
    "PPH22": "https://www.pajak.go.id/id/pph-pasal-22",
    "PPH23": "https://pajak.go.id/id/pph-pasal-2326",
    "PPH26": "https://pajak.go.id/id/pph-pasal-2326",
    "PPH4_2": "https://pajak.go.id/id/pemotongan-pajak-penghasilan-pasal-4-ayat-2-3",
    "PPN": "https://pajak.go.id/index.php/id/siaran-pers/pemerintah-terbitkan-aturan-dpp-nilai-lain-dan-besaran-tertentu-ppn",
}
TAX_OPTIONS = [
    dict(id="NO_TAX", tax_type="NO_TAX", label="Tanpa pajak", rate_percent="0", note="Tidak menambahkan baris pajak."),
    dict(
        id="PPH21_MANUAL",
        tax_type="PPH21",
        label="PPh 21 — tarif manual / TER",
        rate_percent=None,
        note=(
            "Tarif dan dasar pengenaan bergantung pada penerima, jenis penghasilan, dan periode. "
            "Masukkan tarif yang sesuai."
        ),
    ),
    dict(
        id="PPH22_PROCUREMENT",
        tax_type="PPH22",
        label="PPh 22 — pembelian pemerintah/BUMN, 1,5%",
        rate_percent="1.5",
        note="Untuk pembelian yang memenuhi ketentuan pemungutan PPh 22; basis tidak termasuk PPN.",
    ),
    dict(
        id="PPH22_IMPORT_API",
        tax_type="PPH22",
        label="PPh 22 — impor dengan API, 2,5%",
        rate_percent="2.5",
        note="Hanya untuk impor yang sesuai kategori tarif ini. Sesuaikan basis dengan nilai impor.",
    ),
    dict(
        id="PPH22_MANUAL",
        tax_type="PPH22",
        label="PPh 22 — tarif manual",
        rate_percent=None,
        note="Jenis barang, pemungut, impor, dan status wajib pajak memengaruhi tarif.",
    ),
    dict(
        id="PPH23_SERVICES",
        tax_type="PPH23",
        label="PPh 23 — jasa/sewa harta, 2%",
        rate_percent="2",
        note="Untuk jasa/sewa harta yang menjadi objek PPh 23; tidak termasuk sewa tanah/bangunan.",
    ),
    dict(
        id="PPH23_PASSIVE",
        tax_type="PPH23",
        label="PPh 23 — bunga/royalti/hadiah, 15%",
        rate_percent="15",
        note="Untuk penghasilan yang memenuhi objek PPh 23. Periksa pengecualian dan status penerima.",
    ),
    dict(
        id="PPH23_MANUAL",
        tax_type="PPH23",
        label="PPh 23 — tarif manual",
        rate_percent=None,
        note="Gunakan untuk penyesuaian tarif sesuai status atau ketentuan penerima.",
    ),
    dict(
        id="PPH26_MANUAL",
        tax_type="PPH26",
        label="PPh 26 — penerima luar negeri, tarif manual",
        rate_percent=None,
        note="Tarif/basis bergantung pada jenis penghasilan dan perjanjian pajak yang berlaku.",
    ),
    dict(
        id="PPH4_2_RENT",
        tax_type="PPH4_2",
        label="PPh 4(2) — sewa tanah/bangunan, 10%",
        rate_percent="10",
        note="Dihitung atas jumlah bruto sewa tanah dan/atau bangunan yang menjadi objek pajak.",
    ),
    dict(
        id="PPH4_2_MANUAL",
        tax_type="PPH4_2",
        label="PPh 4(2) — pajak final, tarif manual",
        rate_percent=None,
        note="Pilih tarif sesuai objek pajak final, misalnya konstruksi. Tidak memakai satu tarif umum.",
    ),
    dict(
        id="PPN_NON_LUXURY",
        tax_type="PPN",
        label="PPN — 12% × 11/12 DPP (efektif 11%)",
        rate_percent="11",
        note=(
            "Untuk transaksi nonmewah yang menggunakan DPP nilai lain 11/12. "
            "Basis di sini adalah nilai sebelum penyesuaian DPP."
        ),
    ),
    dict(
        id="PPN_LUXURY",
        tax_type="PPN",
        label="PPN — 12% atas DPP penuh",
        rate_percent="12",
        note="Untuk transaksi yang dikenai tarif 12% atas DPP penuh; bukan pilihan umum bagi semua barang.",
    ),
    dict(
        id="PPN_MANUAL",
        tax_type="PPN",
        label="PPN — tarif efektif manual",
        rate_percent=None,
        note="Untuk skema DPP/besaran tertentu atau periode lain. Masukkan tarif efektif dan basis yang sesuai.",
    ),
]
OPTIONS_BY_ID = {option["id"]: option for option in TAX_OPTIONS}
TAX_LABELS = {
    "PPH21": "PPh 21",
    "PPH22": "PPh 22",
    "PPH23": "PPh 23",
    "PPH26": "PPh 26",
    "PPH4_2": "PPh 4(2)",
    "PPN": "PPN",
    "NO_TAX": "Tanpa pajak",
    "PPH25": "PPh 25",
}


def amount(base, rate):
    return (Decimal(str(base)) * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def effect_of(assignment):
    return assignment.tax_effect or "ADD"


def assignment_dict(assignment):
    return dict(
        tax_type=assignment.tax_type.value,
        profile=assignment.rate_profile,
        rate_percent=str(Decimal(str(assignment.tax_rate)) * 100),
        base_amount=str(assignment.tax_base) if assignment.tax_base is not None else None,
        base_is_manual=assignment.base_is_manual,
        effect=effect_of(assignment),
        amount=str(assignment.tax_amount),
    )


async def load_assignments(db, items):
    ids = [item.line_item_id for item in items]
    if not ids:
        return {}
    rows = (await db.execute(select(TaxAssignment).where(TaxAssignment.line_item_id.in_(ids)))).scalars().all()
    return {str(row.line_item_id): row for row in rows}


async def refresh_assignments(db, draft_id):
    items = (await db.execute(select(LineItem).where(LineItem.draft_id == draft_id))).scalars().all()
    assignments = await load_assignments(db, items)
    for item in items:
        tax = assignments.get(str(item.line_item_id))
        if tax and not tax.base_is_manual:
            if item.line_total is None:
                raise ValueError("An item with tax needs a line total or a manually entered tax base.")
            tax.tax_base = Decimal(str(item.line_total))
            tax.tax_amount = amount(tax.tax_base, tax.tax_rate)
    await db.flush()
    return items, assignments


def totals(req, header, items, assignments):
    base = (
        sum((Decimal(str(i.line_total)) for i in items), Decimal("0"))
        if items and all(i.line_total is not None for i in items)
        else None
    )
    added = sum((Decimal(str(t.tax_amount)) for t in assignments.values() if effect_of(t) == "ADD"), Decimal("0"))
    withheld = sum((Decimal(str(t.tax_amount)) for t in assignments.values() if effect_of(t) == "DEDUCT"), Decimal("0"))
    fees = Decimal(str(header.fee_amount or 0)) if header else Decimal("0")
    rounding = Decimal(str(header.rounding_adj or 0)) if header else Decimal("0")
    manual = (
        getattr(getattr(req, "status", None), "value", None) == "READY_FOR_REFINEMENT"
        or bool(getattr(req, "refinement_submitted_at", None))
        or bool(assignments)
    )
    total = base + added - withheld + fees + rounding if manual and base is not None else None
    if not manual:
        total = Decimal(str(header.total_amount)) if header and header.total_amount is not None else base
    return dict(
        base_amount=str(base) if base is not None else None,
        added_tax=str(added),
        withheld_tax=str(withheld),
        fees=str(fees),
        rounding=str(rounding),
        voucher_total=str(total) if total is not None else None,
        calculated_from_items=manual,
    )
