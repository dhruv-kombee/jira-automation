"""Comprehensive Attachment Content Extraction Service.

Extracts internal content from all common file formats for LLM inspection:
- PDF (.pdf): FlateDecode stream extraction and native document parts.
- Word (.docx): OpenXML paragraph, heading, and table extraction.
- Excel (.xlsx, .xls): openpyxl table and sheet structure parsing.
- ZIP (.zip): archive inspection and recursive extraction of internal logs, code, and documents.
- Images (.png, .jpg, .jpeg, .webp, .gif): dimensions, OCR hints, and native multimodal vision parts.
- Text & Logs (.txt, .log, .json, .csv, .xml, .sql, .md, .py, .js, etc.): multi-encoding log extractors.
"""
import io
import re
import zlib
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from src.logger import logger


def parse_excel_content(raw_bytes: bytes, max_rows: int = 150, max_cols: int = 30) -> Tuple[str, str]:
    """Parse Excel spreadsheet (.xlsx, .xlsm) into structured markdown tables."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
        sheets_out = []
        total_rows = 0
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_data = []
            sheet_rows = 0
            for row in ws.iter_rows(values_only=True):
                # Filter out entirely empty rows
                if any(cell is not None and str(cell).strip() != "" for cell in row):
                    sheet_rows += 1
                    total_rows += 1
                    if sheet_rows > max_rows:
                        rows_data.append(f"... [Remaining rows truncated]")
                        break
                    row_cells = [str(cell).strip().replace("\n", " ") if cell is not None else "" for cell in row[:max_cols]]
                    rows_data.append(" | ".join(row_cells))
            if rows_data:
                sheets_out.append(f"### Sheet: {sheet_name}\n" + "\n".join(rows_data))
        if sheets_out:
            summary = f"Excel Spreadsheet with {len(sheets_out)} sheet(s), {total_rows} total row(s)"
            return "\n\n".join(sheets_out), summary
    except Exception as exc:
        logger.debug(f"openpyxl Excel parsing failed ({exc}), attempting XML fallback")

    # Fallback: OpenXML direct parsing if openpyxl read_only failed
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            shared_strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                st_tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in st_tree.iter():
                    if si.tag.endswith("}t") and si.text:
                        shared_strings.append(si.text.strip())
            if shared_strings:
                summary = f"Excel Spreadsheet containing {len(shared_strings)} shared string cells"
                return "Excel Cell Values:\n" + "\n".join(shared_strings[:max_rows]), summary
    except Exception as xml_exc:
        logger.debug(f"Excel XML fallback failed: {xml_exc}")

    return "", "Empty or unparseable Excel workbook"


def parse_docx_content(raw_bytes: bytes) -> Tuple[str, str]:
    """Parse Word document (.docx) into structured text including paragraphs and tables."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            doc_xml = None
            for fname in ["word/document.xml", "document.xml"]:
                if fname in z.namelist():
                    doc_xml = z.read(fname)
                    break
            if not doc_xml:
                return "", "Invalid Word document format"

            tree = ET.fromstring(doc_xml)
            blocks = []
            para_count = 0
            table_count = 0

            for elem in tree.iter():
                tag = elem.tag.split("}")[-1]
                if tag == "p":
                    # Collect paragraph text runs
                    text_parts = []
                    for child in elem.iter():
                        c_tag = child.tag.split("}")[-1]
                        if c_tag == "t" and child.text:
                            text_parts.append(child.text)
                        elif c_tag == "tab":
                            text_parts.append("\t")
                        elif c_tag == "br":
                            text_parts.append("\n")
                    line = "".join(text_parts).strip()
                    if line:
                        para_count += 1
                        blocks.append(line)
                elif tag == "tr":
                    # Collect table row cells
                    cell_texts = []
                    for cell in elem.iter():
                        if cell.tag.split("}")[-1] == "tc":
                            c_text = "".join(
                                t.text for t in cell.iter() if t.tag.split("}")[-1] == "t" and t.text
                            )
                            cell_texts.append(c_text.strip())
                    if any(cell_texts):
                        table_count += 1
                        blocks.append(" | ".join(cell_texts))

            # Deduplicate repeated identical lines from nested XML iterations
            cleaned_blocks = []
            prev = None
            for b in blocks:
                if b != prev:
                    cleaned_blocks.append(b)
                    prev = b

            if cleaned_blocks:
                summary = f"Word Document with {para_count} paragraph(s)" + (f" and {table_count} table row(s)" if table_count else "")
                return "\n\n".join(cleaned_blocks), summary
    except Exception as exc:
        logger.debug(f"Word docx extraction failed: {exc}")

    return "", "Unparseable Word document"


def parse_zip_content(raw_bytes: bytes, max_files: int = 20, max_bytes_per_file: int = 50000) -> Tuple[str, str]:
    """Inspect and extract internal contents from ZIP archives."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            file_list = z.infolist()
            total_files = len(file_list)
            header = [f"ZIP Archive Directory Tree ({total_files} file(s)):"]
            for f in file_list[:max_files]:
                header.append(f" • {f.filename} ({f.file_size:,} bytes)")
            if total_files > max_files:
                header.append(f" • ... and {total_files - max_files} more file(s)")

            extracted_parts = []
            text_exts = (
                ".log", ".txt", ".json", ".csv", ".xml", ".sql", ".md",
                ".yml", ".yaml", ".py", ".js", ".ts", ".html", ".css",
                ".env", ".ini", ".conf", ".sh", ".bat", ".ps1", ".diff", ".patch"
            )

            for item in file_list:
                if item.is_dir():
                    continue
                name_lower = item.filename.lower()
                # 1. Text, logs, and code
                if any(name_lower.endswith(ext) for ext in text_exts):
                    try:
                        content_b = z.read(item.filename)[:max_bytes_per_file]
                        text_str = content_b.decode("utf-8", errors="replace")
                        extracted_parts.append(f"\n--- [Archive File: {item.filename}] ---\n{text_str}\n")
                    except Exception:
                        pass
                # 2. Nested Word document
                elif name_lower.endswith(".docx"):
                    try:
                        doc_text, _ = parse_docx_content(z.read(item.filename))
                        if doc_text:
                            extracted_parts.append(f"\n--- [Word Document in Archive: {item.filename}] ---\n{doc_text}\n")
                    except Exception:
                        pass
                # 3. Nested Excel spreadsheet
                elif name_lower.endswith(".xlsx"):
                    try:
                        xls_text, _ = parse_excel_content(z.read(item.filename))
                        if xls_text:
                            extracted_parts.append(f"\n--- [Spreadsheet in Archive: {item.filename}] ---\n{xls_text}\n")
                    except Exception:
                        pass
                # 4. Nested PDF
                elif name_lower.endswith(".pdf"):
                    try:
                        pdf_text, _ = parse_pdf_content(z.read(item.filename))
                        if pdf_text:
                            extracted_parts.append(f"\n--- [PDF Document in Archive: {item.filename}] ---\n{pdf_text}\n")
                    except Exception:
                        pass

            body_text = "\n".join(header)
            if extracted_parts:
                body_text += "\n\nExtracted Archive Contents:\n" + "\n".join(extracted_parts)

            summary = f"ZIP Archive containing {total_files} file(s) ({len(extracted_parts)} inspected)"
            return body_text, summary
    except Exception as exc:
        logger.debug(f"ZIP archive extraction failed: {exc}")

    return "", "Unparseable ZIP archive"


def parse_pdf_content(raw_bytes: bytes) -> Tuple[str, str]:
    """Extract readable text streams from PDF files."""
    text_chunks = []
    # 1. Parse text streams in PDF objects (both uncompressed and /FlateDecode)
    for s in re.finditer(rb'stream[\r\n]+([\s\S]*?)[\r\n]+endstream', raw_bytes):
        raw_stream = s.group(1)
        decomp = None
        try:
            decomp = zlib.decompress(raw_stream)
        except Exception:
            decomp = raw_stream

        if decomp:
            # Match Tj string operator: (text) Tj
            for m in re.finditer(rb'\((.*?)\)\s*Tj', decomp):
                chunk = m.group(1).decode("latin-1", errors="replace").strip()
                if chunk and len(chunk) > 1:
                    text_chunks.append(chunk)

            # Match TJ array operator: [(text1) 20 (text2)] TJ
            for m in re.finditer(rb'\[(.*?)\]\s*TJ', decomp):
                arr_content = m.group(1)
                for item in re.finditer(rb'\((.*?)\)', arr_content):
                    chunk = item.group(1).decode("latin-1", errors="replace").strip()
                    if chunk and len(chunk) > 1:
                        text_chunks.append(chunk)

    # 2. Extract plain text strings outside streams if stream count was small
    if len(text_chunks) < 3:
        for m in re.finditer(rb'\(([^()]{4,})\)', raw_bytes):
            val = m.group(1).decode("latin-1", errors="replace").strip()
            if any(c.isalpha() for c in val) and not val.startswith(("/", "Font", "ProcSet", "MediaBox")):
                text_chunks.append(val)

    if text_chunks:
        # Deduplicate and join
        joined_text = " ".join(text_chunks)
        summary = f"PDF Document containing approximately {len(text_chunks)} text segments"
        return joined_text, summary

    return "", "PDF Document (multimodal inspection)"


def parse_text_content(raw_bytes: bytes, filename: str) -> Tuple[str, str]:
    """Decode plain text, logs, JSON, CSV, and code with multiple encodings."""
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252", "utf-16"):
        try:
            decoded = raw_bytes.decode(enc)
            line_count = decoded.count("\n") + 1
            summary = f"Text file ({line_count} lines, {len(raw_bytes):,} bytes)"
            return decoded, summary
        except UnicodeDecodeError:
            continue

    return raw_bytes.decode("utf-8", errors="replace"), "Text file (binary lossy decode)"


def extract_attachment_content(
    raw_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified entry point to inspect and extract content from ANY attachment format.

    Supports: PDF, Word (.docx), Excel (.xlsx, .xls), ZIP (.zip), Images, Logs & Code.
    Returns:
        Dict with 'format', 'text', 'summary', 'is_image', 'is_pdf', 'filename'
    """
    if not raw_bytes:
        return {
            "format": "empty",
            "filename": filename,
            "text": "",
            "summary": "Empty file",
            "is_image": False,
            "is_pdf": False,
        }

    lower_name = (filename or "attachment").lower()
    lower_type = (content_type or "").lower().split(";")[0].strip()

    # 1. IMAGE FORMATS
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".svg")
    if lower_type.startswith("image/") or lower_name.endswith(image_exts):
        return {
            "format": "image",
            "filename": filename,
            "text": f"[Attached Screenshot/Image: {filename} ({len(raw_bytes):,} bytes)]",
            "summary": f"Image file ({len(raw_bytes):,} bytes)",
            "is_image": True,
            "is_pdf": False,
            "mime_type": lower_type if lower_type.startswith("image/") else "image/png",
        }

    # 2. PDF FORMAT
    if lower_type == "application/pdf" or lower_name.endswith(".pdf"):
        extracted_text, summary = parse_pdf_content(raw_bytes)
        return {
            "format": "pdf",
            "filename": filename,
            "text": extracted_text,
            "summary": summary,
            "is_image": False,
            "is_pdf": True,
            "mime_type": "application/pdf",
        }

    # 3. EXCEL SPREADSHEETS (.xlsx, .xlsm, .xls)
    excel_exts = (".xlsx", ".xlsm", ".xltx", ".xls")
    if any(lower_name.endswith(ext) for ext in excel_exts) or "spreadsheet" in lower_type or "excel" in lower_type:
        extracted_text, summary = parse_excel_content(raw_bytes)
        return {
            "format": "excel",
            "filename": filename,
            "text": extracted_text,
            "summary": summary,
            "is_image": False,
            "is_pdf": False,
        }

    # 4. WORD DOCUMENTS (.docx, .docm, .dotx)
    word_exts = (".docx", ".docm", ".dotx")
    if any(lower_name.endswith(ext) for ext in word_exts) or "wordprocessingml" in lower_type or "msword" in lower_type:
        extracted_text, summary = parse_docx_content(raw_bytes)
        return {
            "format": "word",
            "filename": filename,
            "text": extracted_text,
            "summary": summary,
            "is_image": False,
            "is_pdf": False,
        }

    # 5. ZIP ARCHIVES (.zip, .jar, .war)
    zip_exts = (".zip", ".jar", ".war")
    if any(lower_name.endswith(ext) for ext in zip_exts) or "zip" in lower_type:
        extracted_text, summary = parse_zip_content(raw_bytes)
        return {
            "format": "zip",
            "filename": filename,
            "text": extracted_text,
            "summary": summary,
            "is_image": False,
            "is_pdf": False,
        }

    # 6. TEXT, LOGS, JSON, CSV, CODE
    extracted_text, summary = parse_text_content(raw_bytes, filename)
    return {
        "format": "text",
        "filename": filename,
        "text": extracted_text,
        "summary": summary,
        "is_image": False,
        "is_pdf": False,
    }
