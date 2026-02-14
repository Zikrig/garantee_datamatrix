from __future__ import annotations

import re
import io
from dataclasses import dataclass
from typing import Iterable, Any

import pdfplumber
import io
from typing import Iterable, Any


@dataclass
class ReceiptItem:
    name: str
    quantity: float
    amount: float
    raw: str


@dataclass
class ReceiptData:
    date: str | None
    items: list[ReceiptItem]
    raw_text: str


class ReceiptParser:
    DATE_RE = re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}[./-]\d{2}[./-]\d{2,4}\b"
    )
    NUM_RE = re.compile(r"\d+[.,]\d{2}|\d+")

    def parse_pdf(self, file_source: str | io.BytesIO) -> ReceiptData:
        with pdfplumber.open(file_source) as pdf:
            page = pdf.pages[0]
            text = page.extract_text() or ""
            date = self._extract_date(page, text)
            items = self._extract_items(text)
        return ReceiptData(date=date, items=items, raw_text=text)

    def _extract_date(self, page, text: str) -> str | None:
        words = page.extract_words()
        if words:
            top_limit = page.height * 0.25
            right_limit = page.width * 0.55
            candidates: list[tuple[float, str]] = []
            for w in words:
                if w["top"] <= top_limit and w["x0"] >= right_limit:
                    match = self.DATE_RE.search(w["text"])
                    if match:
                        candidates.append((w["x0"], match.group(0)))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]

        match = self.DATE_RE.search(text)
        return match.group(0) if match else None

    def _extract_items(self, text: str) -> list[ReceiptItem]:
        items: list[ReceiptItem] = []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if self._is_total_line(line):
                continue
            item = self._parse_item_line(line)
            if item:
                items.append(item)
        return items

    def _is_total_line(self, line: str) -> bool:
        low = line.lower()
        return any(
            key in low
            for key in (
                "итог",
                "итого",
                "к оплате",
                "сумма",
                "наличные",
                "безнал",
                "смена",
                "касса",
                "налог",
                "ндс",
            )
        )

    def _parse_item_line(self, line: str) -> ReceiptItem | None:
        normalized = " ".join(line.replace("×", "x").replace("х", "x").split())

        # Pattern: "Название 2 x 199,00 398,00"
        m = re.match(
            r"^(?P<name>.+?)\s+(?P<qty>\d+[.,]?\d*)\s*x\s*(?P<price>\d+[.,]\d{2})\s+(?P<sum>\d+[.,]\d{2})$",
            normalized,
        )
        if m:
            return ReceiptItem(
                name=m.group("name").strip(),
                quantity=float(m.group("qty").replace(",", ".")),
                amount=float(m.group("sum").replace(",", ".")),
                raw=line,
            )

        # Pattern: "Название 2 шт 398,00"
        m = re.match(
            r"^(?P<name>.+?)\s+(?P<qty>\d+[.,]?\d*)\s*(шт|pcs)?\s+(?P<sum>\d+[.,]\d{2})$",
            normalized,
        )
        if m:
            return ReceiptItem(
                name=m.group("name").strip(),
                quantity=float(m.group("qty").replace(",", ".")),
                amount=float(m.group("sum").replace(",", ".")),
                raw=line,
            )

        # Heuristic: item index + name + price + qty + amount
        tokens = normalized.split()
        number_positions = [(idx, t) for idx, t in enumerate(tokens) if self.NUM_RE.fullmatch(t)]
        if len(number_positions) >= 2:
            idx_amount, amount_raw = number_positions[-1]
            idx_qty = None
            qty_raw = None
            if tokens[number_positions[-2][0]].isdigit():
                idx_qty, qty_raw = number_positions[-2]
            elif len(number_positions) >= 3 and tokens[number_positions[-3][0]].isdigit():
                idx_qty, qty_raw = number_positions[-3]

            idx_price = number_positions[-2][0] if idx_qty else number_positions[-2][0]
            name_start = 1 if tokens[0].isdigit() else 0
            name_end = idx_price
            name = " ".join(tokens[name_start:name_end]).strip()

            if name and self._looks_like_amount(amount_raw):
                quantity = float(qty_raw.replace(",", ".")) if qty_raw else 1.0
                return ReceiptItem(
                    name=name,
                    quantity=quantity,
                    amount=float(amount_raw.replace(",", ".")),
                    raw=line,
                )
        return None

    def _looks_like_amount(self, value: str) -> bool:
        return bool(re.match(r"^\d+[.,]\d{2}$", value))


def render_items(items: Iterable[ReceiptItem]) -> str:
    rows = []
    for item in items:
        rows.append(f"- {item.name} — {item.quantity} шт — {item.amount:.2f}")
    return "\n".join(rows) if rows else "(не найдено)"


def parse_receipt_pdf(file_source: str | io.BytesIO) -> list[dict[str, Any]] | None:
    parser = ReceiptParser()
    try:
        data = parser.parse_pdf(file_source)
        if not data.items:
            return None
        return [{"name": item.name, "price": item.amount} for item in data.items]
    except Exception as e:
        import logging
        logging.error(f"Error parsing receipt: {e}")
        return None

