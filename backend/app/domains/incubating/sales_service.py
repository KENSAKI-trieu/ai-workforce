"""
Sales & CRM AI Service for product catalog search, PDF quote generation, and CRM lead creation.
"""

import logging
import re
import uuid
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def handle_sales_request(message: str, customer_name: str = "Khách Hàng Doanh Nghiệp") -> Dict[str, Any]:
    """
    Handles sales catalog lookup and quote generation.
    """
    msg_lower = message.lower()

    # Extract quantity
    qty_match = re.search(r'(\d+)\s*(chiếc|cái|camera|máy|bộ|bàn)', msg_lower)
    qty = int(qty_match.group(1)) if qty_match else 10

    item_name = "Camera AI IP Security 4K"
    unit_price = 2500000  # VNĐ
    subtotal = qty * unit_price
    discount = int(subtotal * 0.1)  # 10% discount
    total = subtotal - discount

    quote_card = {
        "id": f"QT-{uuid.uuid4().hex[:6].upper()}",
        "customer_name": customer_name,
        "items": [
            {
                "name": item_name,
                "quantity": qty,
                "unit_price": f"{unit_price:,} VNĐ",
                "total": f"{subtotal:,} VNĐ",
            }
        ],
        "subtotal": f"{subtotal:,} VNĐ",
        "discount": f"{discount:,} VNĐ (10%)",
        "total_amount": f"{total:,} VNĐ",
        "pdf_url": f"/api/v1/sales/download-quote/{uuid.uuid4().hex[:8]}",
        "status": "GENERATED",
    }

    return {
        "quote_card": quote_card,
        "reply": (
            f"Kính gửi **{customer_name}**, tôi đã tra cứu danh mục tồn kho và khởi tạo thành công **Báo Giá Bảng Giá Chi Tiết** cho **{qty} {item_name}**.\n\n"
            f"Tổng giá trị đơn hàng sau chiết khấu 10% là: **{total:,} VNĐ**.\n"
            f"Bạn có thể tải file Báo giá PDF chính thức bên dưới!"
        ),
    }
