"""Comprehensive Test Suite for All Attachment Formats:
PDF, Plain Text / Log, Image, ZIP, Excel (.xlsx), Word (.docx).
"""
import sys
sys.path.insert(0, ".")
import io
import asyncio
import zipfile
import openpyxl
from PIL import Image

from src.services.attachment_parser import extract_attachment_content
from src.services.ai_service import extract_jira_ticket


def create_sample_excel() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sprint_Defects"
    ws.append(["Defect ID", "Component", "Severity", "Description", "Steps to Reproduce"])
    ws.append([
        "DEF-901",
        "Payment Gateway",
        "Critical",
        "Checkout returns 504 Gateway Timeout during Stripe token exchange",
        "1. Add item to cart 2. Click Checkout 3. Wait 30s for timeout"
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def create_sample_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>QA Bug Specification: User Avatar Upload Fails</w:t></w:r></w:p>
    <w:p><w:r><w:t>When users upload a PNG profile picture larger than 2MB, frontend crashes with Uncaught TypeError.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Browser</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Chrome 122 on macOS</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"""
        z.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def create_sample_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "nginx_error.log",
            "2026-10-06 18:30:12 [error] 1421#1421: *88 connect() failed (111: Connection refused) while connecting to upstream auth_cluster:8080"
        )
        z.writestr(
            "stacktrace.txt",
            "Traceback (most recent call last):\n  File 'app/auth.py', line 45, in verify_token\n    raise ConnectionRefusedError('Auth cluster unreachable')\nConnectionRefusedError: Auth cluster unreachable"
        )
    return buf.getvalue()


def create_sample_pdf() -> bytes:
    content_stream = b"BT /F1 14 Tf 50 700 Td (Incident Report: Database pool exhausted with 500 Internal Error) Tj ET\n"
    stream_len = len(content_stream)
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    obj4 = f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1") + content_stream + b"endstream\nendobj\n"
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    header = b"%PDF-1.4\n"
    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    offset3 = offset2 + len(obj2)
    offset4 = offset3 + len(obj3)
    offset5 = offset4 + len(obj4)
    xref_offset = offset5 + len(obj5)

    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        + f"{offset1:010d} 00000 n \n".encode("latin-1")
        + f"{offset2:010d} 00000 n \n".encode("latin-1")
        + f"{offset3:010d} 00000 n \n".encode("latin-1")
        + f"{offset4:010d} 00000 n \n".encode("latin-1")
        + f"{offset5:010d} 00000 n \n".encode("latin-1")
    )
    trailer = b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref_offset).encode("latin-1") + b"\n%%EOF\n"

    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref + trailer


def create_sample_image() -> bytes:
    img = Image.new("RGB", (300, 100), color=(220, 38, 38))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_sample_text_log() -> bytes:
    return (
        b"2026-10-06 22:15:00 CRITICAL [BillingService] StripeCardError: Your card was declined. Code: card_declined\n"
        b"Endpoint: POST /api/v1/charge (HTTP 402 Payment Required)\n"
    )


async def run_all_format_tests():
    print("=" * 60)
    print("RUNNING MULTI-FORMAT ATTACHMENT EXTRACTION TEST SUITE")
    print("=" * 60)

    # 1. TEST EXCEL EXTRACTION
    excel_b = create_sample_excel()
    parsed_excel = extract_attachment_content(excel_b, "Sprint_Defects.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert parsed_excel["format"] == "excel"
    assert "DEF-901" in parsed_excel["text"]
    assert "Checkout returns 504 Gateway Timeout" in parsed_excel["text"]
    print("✅ 1. Excel (.xlsx) Parser: PASSED")

    ticket_excel = await extract_jira_ticket(
        "Please check the attached Excel bug list and create ticket.",
        sender_name="QA Lead",
        sender_role="CLIENT",
        attachments=[{"name": "Sprint_Defects.xlsx", "bytes": excel_b, "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}],
    )
    assert ticket_excel["is_ticket_request"] is True
    print(f"   -> AI Extracted Summary: {ticket_excel.get('summary')}")

    # 2. TEST WORD (.DOCX) EXTRACTION
    docx_b = create_sample_docx()
    parsed_docx = extract_attachment_content(docx_b, "Avatar_Bug.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert parsed_docx["format"] == "word"
    assert "Avatar Upload Fails" in parsed_docx["text"]
    assert "Chrome 122 on macOS" in parsed_docx["text"]
    print("✅ 2. Word (.docx) Parser: PASSED")

    ticket_docx = await extract_jira_ticket(
        "Attached the full reproduction document for the avatar issue.",
        sender_name="Musaib",
        sender_role="CLIENT",
        attachments=[{"name": "Avatar_Bug.docx", "bytes": docx_b, "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}],
    )
    assert ticket_docx["is_ticket_request"] is True
    print(f"   -> AI Extracted Summary: {ticket_docx.get('summary')}")

    # 3. TEST ZIP ARCHIVE EXTRACTION
    zip_b = create_sample_zip()
    parsed_zip = extract_attachment_content(zip_b, "server_diagnostics.zip", "application/zip")
    assert parsed_zip["format"] == "zip"
    assert "nginx_error.log" in parsed_zip["text"]
    assert "Connection refused" in parsed_zip["text"]
    assert "Auth cluster unreachable" in parsed_zip["text"]
    print("✅ 3. ZIP (.zip) Archive Parser: PASSED")

    ticket_zip = await extract_jira_ticket(
        "Logs from production crash attached in zip.",
        sender_name="DevOps",
        sender_role="CLIENT",
        attachments=[{"name": "server_diagnostics.zip", "bytes": zip_b, "content_type": "application/zip"}],
    )
    assert ticket_zip["is_ticket_request"] is True
    print(f"   -> AI Extracted Summary: {ticket_zip.get('summary')}")

    # 4. TEST PDF EXTRACTION
    pdf_b = create_sample_pdf()
    parsed_pdf = extract_attachment_content(pdf_b, "Incident_Report.pdf", "application/pdf")
    assert parsed_pdf["format"] == "pdf"
    assert parsed_pdf["is_pdf"] is True
    assert "Database pool exhausted" in parsed_pdf["text"]
    print("✅ 4. PDF (.pdf) Parser: PASSED")

    ticket_pdf = await extract_jira_ticket(
        "Incident summary in PDF.",
        sender_name="Client Support",
        sender_role="CLIENT",
        attachments=[{"name": "Incident_Report.pdf", "bytes": pdf_b, "content_type": "application/pdf"}],
    )
    assert ticket_pdf["is_ticket_request"] is True
    print(f"   -> AI Extracted Summary: {ticket_pdf.get('summary')}")

    # 5. TEST IMAGE EXTRACTION
    img_b = create_sample_image()
    parsed_img = extract_attachment_content(img_b, "error_screen.png", "image/png")
    assert parsed_img["format"] == "image"
    assert parsed_img["is_image"] is True
    print("✅ 5. Image (.png/.jpg) Multimodal Parser: PASSED")

    ticket_img = await extract_jira_ticket(
        "See attached screenshot of the red error banner on checkout page.",
        sender_name="Dhruv",
        sender_role="CLIENT",
        attachments=[{"name": "error_screen.png", "bytes": img_b, "content_type": "image/png"}],
    )
    assert ticket_img["is_ticket_request"] is True
    print(f"   -> AI Extracted Summary: {ticket_img.get('summary')}")

    # 6. TEST TEXT / LOG FILE EXTRACTION
    log_b = create_sample_text_log()
    parsed_log = extract_attachment_content(log_b, "billing_error.log", "text/plain")
    assert parsed_log["format"] == "text"
    assert "card_declined" in parsed_log["text"]
    print("✅ 6. Text / Log (.log/.txt) Parser: PASSED")

    ticket_log = await extract_jira_ticket(
        "Attached billing server log.",
        sender_name="Santosh",
        sender_role="CLIENT",
        attachments=[{"name": "billing_error.log", "bytes": log_b, "content_type": "text/plain"}],
    )
    assert ticket_log["is_ticket_request"] is True
    print(f"   -> AI Extracted Summary: {ticket_log.get('summary')}")

    print("=" * 60)
    print("🎉 ALL 6 FORMATS (PDF, TEXT, IMAGE, ZIP, EXCEL, WORD) TESTED & VERIFIED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_format_tests())
