---
name: pdf-reader
description: "Use this skill whenever you need to read, inspect, or extract content from PDF files. This includes: viewing PDF metadata and structure (page count, title, author), extracting text content from all or specific pages, extracting tables from PDFs, understanding the contents of complex or long PDF documents, reading scanned PDFs via OCR, or any task where the user hands you a PDF and wants to know what's in it. Trigger when the user mentions reading, opening, inspecting, or understanding a PDF file — even casually like 'what's in this PDF', 'read the document', or 'help me understand this report'. Also trigger when the user provides a PDF path and asks questions about its contents, or when the user needs to extract data/tables/text from a PDF for analysis. Do NOT trigger when the primary task is creating, editing, or filling PDF forms, merging/splitting/rotating PDFs, or adding watermarks — this skill is read-only."
---

# PDF Reader

Extract text, tables, and metadata from PDF files — including complex multi-column layouts, long documents, and scanned pages. The bundled script converts PDFs into clean, LLM-readable Markdown using pymupdf4llm (PyMuPDF).

## When to use

You have a bundled script `scripts/pdf_to_text.py` that handles PDF reading. Use it whenever you need to extract content from a PDF — it's much more reliable than writing ad-hoc code, especially for documents with complex layouts, tables, CJK text, styled headings, or many pages.

Claude's built-in PDF reading (via the Read tool) is limited to ~20 pages per request and works by rendering pages as images. The bundled script extracts actual text, handles arbitrarily long documents, and produces structured Markdown with headings, bold text, and tables preserved — output you can reason over directly.

## Setup

The script requires `pymupdf4llm` and `pypdf`:

```bash
pip install pymupdf4llm pypdf
```

For OCR support (scanned PDFs):

```bash
pip install pytesseract pdf2image
# System packages: sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim poppler-utils
```

## Workflow

### Default: Direct extraction

For most tasks, go straight to extraction — no inspection step needed:

```bash
python3 scripts/pdf_to_text.py <file.pdf>
```

This produces clean Markdown with headings, bold text, bullet points, and tables preserved. For specific pages:

```bash
python3 scripts/pdf_to_text.py <file.pdf> --pages 1-10
python3 scripts/pdf_to_text.py <file.pdf> --pages 1,3,5-8
```

Save to file for large outputs:

```bash
python3 scripts/pdf_to_text.py <file.pdf> -o output.md
```

### Figures and charts: skip image descriptions

When converting PDF to Markdown, **do not attempt to describe or transcribe figures, charts, diagrams, or other images**. The script marks image positions with `==> picture [W x H] intentionally omitted <==` — leave these as-is. Figures embedded as images cannot be extracted as text, and spending time describing them adds overhead with little value.

Tables, on the other hand, are extracted and rendered as proper Markdown tables. Always include tables in the output.

### Other output formats

```bash
python3 scripts/pdf_to_text.py <file.pdf> --format text        # Plain text via fitz
```

### Scanned / image-based PDFs

If text extraction returns empty results, the PDF may be scanned:

```bash
python3 scripts/pdf_to_text.py <file.pdf> --ocr
python3 scripts/pdf_to_text.py <file.pdf> --ocr --ocr-lang chi_sim+eng
```

### Inspecting before extraction (optional)

For long or unknown documents where you need to decide which pages matter:

```bash
python3 scripts/pdf_to_text.py <file.pdf> --info        # Metadata: page count, title, etc.
python3 scripts/pdf_to_text.py <file.pdf> --list-pages   # Per-page text length
```

Then extract only the relevant pages. Most useful for documents over 50 pages.

### Strategy for very long documents (50+ pages)

1. Run `--info` to understand page count and structure
2. Extract the first few pages (`--pages 1-5`) to find the table of contents
3. Extract specific sections of interest
4. If needed, extract the full document with `-o output.md` so you can read it in chunks

### Output format

The Markdown output is structured as:

- A `# filename` heading with metadata summary (page count, title, author)
- `## Page N` sections for each page, separated by `---`
- Document headings, bold text, bullet points preserved from the original PDF
- Tables rendered as Markdown tables inline with the text
- Images noted with `==> picture [W x H] intentionally omitted <==`
- Empty pages noted with a `*(No extractable text — this page may be scanned or image-based. Try --ocr)*` hint

## How the script handles tricky PDF patterns

- **Multi-column layouts**: pymupdf4llm preserves reading order correctly across columns.
- **Styled headings and bold text**: Detected and rendered as Markdown `##` headers and `**bold**`.
- **Tables**: Extracted and rendered as inline Markdown tables.
- **CJK text (Chinese/Japanese/Korean)**: Handled natively by PyMuPDF. No special flags needed.
- **Scanned/image-based pages**: Text extraction returns empty. Use `--ocr` to fall back to tesseract. The script reports which pages appear scanned.
- **Encrypted PDFs**: Attempts to open with an empty password (works for many "owner-password-only" PDFs). Reports encryption status if it can't decrypt.
- **Damaged pages**: Errors on individual pages are caught and reported without aborting the entire extraction.

## Options reference

| Flag                | Description                                              |
|---------------------|----------------------------------------------------------|
| `--info`            | Show PDF metadata and exit                               |
| `--list-pages`      | List all pages with dimensions and text length, then exit|
| `--pages <range>`   | Page selection: `1-5`, `1,3,5`, `1-3,7-9` (1-indexed)   |
| `--format md`       | Markdown output with structure preserved (default)       |
| `--format text`     | Plain text output via fitz                               |
| `--max-pages <n>`   | Limit number of pages extracted (0 = unlimited)          |
| `--ocr`             | Use OCR for scanned/image-based pages                    |
| `--ocr-lang <lang>` | OCR language (default: `chi_sim+eng`)                    |
| `-o <path>`         | Write to file instead of stdout                          |
