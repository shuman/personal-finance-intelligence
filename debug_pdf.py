"""Debug script to extract and analyze PDF text."""
import pdfplumber
import pikepdf
import sys

pdf_path = "CBL_AMEX_Gold_100000087858_2390193_23112025.pdf"
password = "844"

# Decrypt first
print("Decrypting PDF...")
with pikepdf.open(pdf_path, password=password) as pdf:
    temp_path = "temp_decrypted.pdf"
    pdf.save(temp_path)
    print(f"Saved decrypted PDF to {temp_path}")

# Extract text
print("\n" + "="*80)
print("EXTRACTING FIRST 2 PAGES TEXT")
print("="*80 + "\n")

with pdfplumber.open(temp_path) as pdf:
    for i, page in enumerate(pdf.pages[:2]):  # First 2 pages
        print(f"\n--- PAGE {i+1} ---\n")
        text = page.extract_text()
        print(text)

print("\n" + "="*80)
print("TABLES ON FIRST PAGE")
print("="*80 + "\n")

with pdfplumber.open(temp_path) as pdf:
    page = pdf.pages[0]
    tables = page.extract_tables()
    print(f"Found {len(tables)} tables")
    for i, table in enumerate(tables):
        print(f"\nTable {i+1}:")
        for row in table[:5]:  # First 5 rows
            print(row)
