import os
import json
import fitz  # PyMuPDF
from pathlib import Path

class TextElement:
    def __init__(self, text, font_size, x_position, y_position, page_width, page_height, 
                 is_bold=False, is_italic=False, width=0, space_above=0, space_below=0):
        self.text = text
        self.font_size = font_size
        self.x_position = x_position
        self.y_position = y_position
        self.page_width = page_width
        self.page_height = page_height
        self.is_bold = is_bold
        self.is_italic = is_italic
        self.width = width
        self.space_above = space_above
        self.space_below = space_below

def extract_text_elements_from_pdf(pdf_path):
    """Extract text elements from PDF using PyMuPDF"""
    doc = fitz.open(pdf_path)
    pdf_pages = []
    
    for page_num in range(min(3, len(doc))):  # Only first 3 pages
        page = doc[page_num]
        page_rect = page.rect
        
        # Get text with formatting info
        text_dict = page.get_text("dict")
        text_elements = []
        
        for block in text_dict["blocks"]:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        # Extract text and formatting
                        text = span["text"].strip()
                        if not text:
                            continue
                            
                        # Get font info
                        font_size = span["size"]
                        font_flags = span["flags"]
                        is_bold = bool(font_flags & 2**4)
                        is_italic = bool(font_flags & 2**1)
                        
                        # Get position (normalize to 0-1 range)
                        bbox = span["bbox"]
                        x_position = bbox[0] / page_rect.width
                        y_position = 1 - (bbox[1] / page_rect.height)  # Flip Y axis
                        width = (bbox[2] - bbox[0]) / page_rect.width
                        
                        # Create text element
                        text_elem = TextElement(
                            text=text,
                            font_size=font_size,
                            x_position=x_position,
                            y_position=y_position,
                            page_width=page_rect.width,
                            page_height=page_rect.height,
                            is_bold=is_bold,
                            is_italic=is_italic,
                            width=width
                        )
                        
                        text_elements.append(text_elem)
        
        # Create page object
        page_obj = type('Page', (), {'text_elements': text_elements})()
        pdf_pages.append(page_obj)
    
    doc.close()
    return pdf_pages

def extract_title(pdf_pages):
    title_candidates = []
    
    for page_num, page in enumerate(pdf_pages):
        if page_num > 2:
            break
            
        for text_element in page.text_elements:
            score = calculate_title_score(text_element, page_num)
            if score > 0:
                title_candidates.append({
                    'text': text_element.text,
                    'score': score,
                    'page': page_num + 1,
                    'font_size': text_element.font_size,
                    'is_bold': text_element.is_bold,
                    'position': f"({text_element.x_position:.2f}, {text_element.y_position:.2f})"
                })
    
    if title_candidates:
        # Sort by score for debugging
        title_candidates.sort(key=lambda x: x['score'], reverse=True)
        return title_candidates[0]['text'], title_candidates
    return "Untitled Document", []

def calculate_title_score(text_element, page_num):
    score = 0
    
    # 1. Font size (primary factor)
    if text_element.font_size >= 18:
        score += 40
    elif text_element.font_size >= 14:
        score += 20
    
    # 2. Page position bonus
    if page_num == 0:  # First page
        score += 25
    elif page_num == 1:  # Second page
        score += 10
    
    # 3. Vertical position on page
    if text_element.y_position > 0.7:  # Top 30% of page
        score += 15
    
    # 4. Horizontal alignment
    if is_centered(text_element):
        score += 15
    elif is_left_aligned(text_element):
        score += 5
    
    # 5. Text formatting
    if text_element.is_bold:
        score += 10
    if text_element.is_italic:
        score += 5
    
    # 6. Text length (titles are usually concise)
    text_length = len(text_element.text.strip())
    if 10 <= text_length <= 100:
        score += 10
    elif text_length > 200:
        score -= 20  # Penalty for very long text
    
    # 7. All caps bonus
    if text_element.text.isupper() and text_length > 5:
        score += 8
    
    # 8. Whitespace isolation
    if has_significant_whitespace_around(text_element):
        score += 10
    
    # 9. Exclude common non-title patterns
    if is_header_footer(text_element):
        score -= 50
    if is_page_number(text_element):
        score -= 50
    
    return score

def is_centered(text_element):
    # Check if text is roughly centered on the page
    text_center = text_element.x_position + (text_element.width / 2)
    page_center = 0.5  # Normalized page center
    return abs(text_center - page_center) < 0.1

def is_left_aligned(text_element):
    return text_element.x_position < 0.2

def has_significant_whitespace_around(text_element):
    # Simple heuristic - if text is isolated vertically
    return text_element.y_position > 0.8 or text_element.y_position < 0.2

def is_header_footer(text_element):
    # Check if text is in header/footer region
    return (text_element.y_position > 0.95 or  # Top 5%
            text_element.y_position < 0.05)    # Bottom 5%

def is_page_number(text_element):
    # Check if text looks like a page number
    text = text_element.text.strip()
    return (text.isdigit() or 
            text.lower().startswith('page') or
            len(text) <= 3)

def process_pdfs():
    # Docker will mount volumes to these paths
    input_dir = Path("/app/input")
    output_dir = Path("/app/output")
    
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get all PDF files
    pdf_files = list(input_dir.glob("*.pdf"))
    
    if not pdf_files:
        print("No PDF files found in /app/input")
        return
    
    for pdf_file in pdf_files:
        try:
            print(f"\n=== Processing {pdf_file.name} ===")
            
            # Extract text elements
            pdf_pages = extract_text_elements_from_pdf(pdf_file)
            
            # Extract title
            title, candidates = extract_title(pdf_pages)
            
            print(f"Extracted Title: '{title}'")
            
            print(f"\nTop 5 Title Candidates:")
            for i, candidate in enumerate(candidates[:5]):
                print(f"  {i+1}. '{candidate['text'][:60]}...' (Score: {candidate['score']}, "
                      f"Font: {candidate['font_size']}, Bold: {candidate['is_bold']}, "
                      f"Page: {candidate['page']}, Pos: {candidate['position']})")
            
            # Show some text elements for debugging
            if pdf_pages and pdf_pages[0].text_elements:
                print(f"\nFirst 10 Text Elements (Page 1):")
                for i, elem in enumerate(pdf_pages[0].text_elements[:10]):
                    print(f"  {i+1}. '{elem.text[:40]}...' (Font: {elem.font_size}, "
                          f"Bold: {elem.is_bold}, Pos: ({elem.x_position:.2f}, {elem.y_position:.2f}))")
            
            # Create JSON data with extracted title
            data = {
                "title": title,
                "outline": []  # Empty for now as requested
            }
            
            # Create output JSON file
            output_file = output_dir / f"{pdf_file.stem}.json"
            with open(output_file, "w") as f:
                json.dump(data, f, indent=2)
            
            print(f"✓ Created {output_file.name}")
            
        except Exception as e:
            print(f"✗ Error processing {pdf_file.name}: {e}")
            import traceback
            traceback.print_exc()
            
            # Create fallback output
            fallback_data = {
                "title": "Untitled Document",
                "outline": []
            }
            output_file = output_dir / f"{pdf_file.stem}.json"
            with open(output_file, "w") as f:
                json.dump(fallback_data, f, indent=2)
            print(f"✓ Created fallback {output_file.name}")

if __name__ == "__main__":
    print("Starting processing pdfs")
    process_pdfs() 
    print("completed processing pdfs")