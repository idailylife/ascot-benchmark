#!/usr/bin/env python3
"""Extract text, tables, and metadata from PDF files.

Usage:
    python3 pdf_to_text.py <file.pdf>                    # Extract all pages as Markdown
    python3 pdf_to_text.py <file.pdf> --info              # Show metadata
    python3 pdf_to_text.py <file.pdf> --list-pages        # List pages with details
    python3 pdf_to_text.py <file.pdf> --pages 1-5         # Extract specific pages
    python3 pdf_to_text.py <file.pdf> --ocr               # OCR for scanned pages

Dependencies:
    Required: pymupdf4llm, pypdf
    Optional (OCR): pytesseract, pdf2image (+ system: tesseract-ocr, poppler-utils)
"""

import argparse
import os
import sys


def parse_page_ranges(spec, total_pages):
    """Parse page range specification like '1-5', '1,3,5', '1-3,7-9' into a sorted list of 0-indexed page numbers."""
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = max(1, int(start))
            end = min(total_pages, int(end))
            for p in range(start, end + 1):
                pages.add(p - 1)
        else:
            p = int(part)
            if 1 <= p <= total_pages:
                pages.add(p - 1)
    return sorted(pages)


def get_pdf_metadata(path):
    """Extract metadata using pypdf."""
    from pypdf import PdfReader

    file_size = os.path.getsize(path)
    reader = PdfReader(path)

    meta = reader.metadata or {}
    info = {
        "file": os.path.basename(path),
        "file_size": file_size,
        "pages": len(reader.pages),
        "title": meta.get("/Title", None),
        "author": meta.get("/Author", None),
        "creator": meta.get("/Creator", None),
        "producer": meta.get("/Producer", None),
        "encrypted": reader.is_encrypted,
        "has_forms": bool(reader.get_fields()),
    }

    if reader.pages:
        box = reader.pages[0].mediabox
        info["page_width"] = float(box.width)
        info["page_height"] = float(box.height)
        info["page_width_mm"] = round(float(box.width) * 0.3528, 1)
        info["page_height_mm"] = round(float(box.height) * 0.3528, 1)

    return info


def format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def show_info(path):
    info = get_pdf_metadata(path)
    lines = [
        f"File:       {info['file']}",
        f"Size:       {format_file_size(info['file_size'])}",
        f"Pages:      {info['pages']}",
        f"Title:      {info.get('title') or '(none)'}",
        f"Author:     {info.get('author') or '(none)'}",
        f"Creator:    {info.get('creator') or '(none)'}",
        f"Producer:   {info.get('producer') or '(none)'}",
        f"Encrypted:  {'Yes' if info['encrypted'] else 'No'}",
        f"Has forms:  {'Yes' if info['has_forms'] else 'No'}",
    ]
    if "page_width_mm" in info:
        lines.append(
            f"Page size:  {info['page_width_mm']} x {info['page_height_mm']} mm "
            f"({info['page_width']:.0f} x {info['page_height']:.0f} pt)"
        )
    print("\n".join(lines))


def list_pages(path):
    """List all pages with dimensions and text length using fitz (PyMuPDF)."""
    import fitz

    doc = fitz.open(path)
    print(f"{'Page':>6}  {'Width':>8}  {'Height':>8}  {'Text chars':>10}")
    print(f"{'----':>6}  {'-----':>8}  {'------':>8}  {'----------':>10}")
    for i, page in enumerate(doc):
        text = page.get_text()
        w_mm = round(page.rect.width * 0.3528, 1)
        h_mm = round(page.rect.height * 0.3528, 1)
        print(f"{i + 1:>6}  {w_mm:>7.1f}  {h_mm:>7.1f}  {len(text):>10}")
    print(f"\nTotal: {doc.page_count} pages")
    doc.close()


def ocr_page(page_image, lang="chi_sim+eng"):
    import pytesseract
    return pytesseract.image_to_string(page_image, lang=lang)


def convert_page_to_image(pdf_path, page_num, dpi=200):
    from pdf2image import convert_from_path
    images = convert_from_path(pdf_path, dpi=dpi, first_page=page_num, last_page=page_num)
    return images[0] if images else None


def extract_markdown(pdf_path, page_indices, use_ocr=False, ocr_lang="chi_sim+eng"):
    """Extract pages as Markdown using pymupdf4llm.

    Returns (markdown_string, scanned_page_numbers).
    """
    import pymupdf4llm

    chunks = pymupdf4llm.to_markdown(pdf_path, pages=page_indices, page_chunks=True)

    output_parts = []
    scanned_pages = []

    for chunk in chunks:
        page_num = chunk["metadata"]["page_number"]
        text = chunk.get("text", "").strip()

        if not text:
            scanned_pages.append(page_num)
            if use_ocr:
                img = convert_page_to_image(pdf_path, page_num)
                if img:
                    ocr_text = ocr_page(img, lang=ocr_lang)
                    if ocr_text and ocr_text.strip():
                        output_parts.append(f"## Page {page_num}\n\n{ocr_text.strip()}\n\n---\n")
                        continue
            output_parts.append(
                f"## Page {page_num}\n\n"
                f"*(No extractable text — this page may be scanned or image-based. Try --ocr)*\n\n---\n"
            )
            continue

        output_parts.append(f"## Page {page_num}\n\n{text}\n\n---\n")

    return "\n".join(output_parts), scanned_pages


def extract_text(pdf_path, page_indices, use_ocr=False, ocr_lang="chi_sim+eng"):
    """Extract pages as plain text using fitz (PyMuPDF)."""
    import fitz

    doc = fitz.open(pdf_path)
    parts = []
    scanned_pages = []

    for idx in page_indices:
        page = doc[idx]
        text = page.get_text().strip()
        page_num = idx + 1

        if not text:
            scanned_pages.append(page_num)
            if use_ocr:
                img = convert_page_to_image(pdf_path, page_num)
                if img:
                    ocr_text = ocr_page(img, lang=ocr_lang)
                    if ocr_text and ocr_text.strip():
                        parts.append(f"--- Page {page_num} ---\n\n{ocr_text.strip()}")
                        continue
            parts.append(f"--- Page {page_num} ---\n\n[No extractable text]")
        else:
            parts.append(f"--- Page {page_num} ---\n\n{text}")

    doc.close()
    return "\n\n".join(parts), scanned_pages


def main():
    parser = argparse.ArgumentParser(description="Extract text and tables from PDF files")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--info", action="store_true", help="Show PDF metadata and exit")
    parser.add_argument("--list-pages", action="store_true", help="List pages with details")
    parser.add_argument("--pages", help="Page range: 1-5, 1,3,5, 1-3,7-9 (1-indexed)")
    parser.add_argument("--format", choices=["md", "text"], default="md",
                        help="Output format (default: md)")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Max pages to extract (0 = unlimited)")
    parser.add_argument("--ocr", action="store_true",
                        help="Use OCR for scanned/image-based pages")
    parser.add_argument("--ocr-lang", default="chi_sim+eng",
                        help="OCR language (default: chi_sim+eng)")
    parser.add_argument("-o", "--output", help="Write to file instead of stdout")

    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        print(f"Error: File not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    if args.info:
        show_info(args.pdf_path)
        return

    if args.list_pages:
        list_pages(args.pdf_path)
        return

    # Get total page count
    import fitz
    try:
        doc = fitz.open(args.pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}", file=sys.stderr)
        sys.exit(1)

    if doc.is_encrypted:
        if not doc.authenticate(""):
            print("Error: PDF is encrypted and requires a password.", file=sys.stderr)
            sys.exit(1)
        print("PDF is encrypted but was unlocked with an empty password.", file=sys.stderr)

    total_pages = doc.page_count
    doc.close()

    # Determine which pages to process
    if args.pages:
        page_indices = parse_page_ranges(args.pages, total_pages)
    else:
        page_indices = list(range(total_pages))

    if args.max_pages > 0:
        page_indices = page_indices[:args.max_pages]

    show_progress = len(page_indices) > 50

    if args.format == "md":
        if show_progress:
            print(f"Extracting {len(page_indices)} pages...", file=sys.stderr)
        # Build document header
        meta = get_pdf_metadata(args.pdf_path)
        header_parts = [f"# {os.path.basename(args.pdf_path)}", ""]
        meta_items = [f"**Pages:** {meta['pages']}"]
        if meta.get("title"):
            meta_items.append(f"**Title:** {meta['title']}")
        if meta.get("author"):
            meta_items.append(f"**Author:** {meta['author']}")
        header_parts.append(" | ".join(meta_items))
        header_parts.append("")
        header_parts.append("---")
        header_parts.append("")
        header = "\n".join(header_parts)

        body, scanned_pages = extract_markdown(
            args.pdf_path, page_indices,
            use_ocr=args.ocr, ocr_lang=args.ocr_lang,
        )
        result = header + body
    else:  # text format
        body, scanned_pages = extract_text(
            args.pdf_path, page_indices,
            use_ocr=args.ocr, ocr_lang=args.ocr_lang,
        )
        result = body

    if scanned_pages and not args.ocr:
        warning = (
            f"Note: {len(scanned_pages)} page(s) appear to be scanned/image-based "
            f"(pages: {', '.join(str(p) for p in scanned_pages[:10])}"
            f"{'...' if len(scanned_pages) > 10 else ''}). "
            f"Use --ocr to extract text via OCR."
        )
        print(warning, file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
