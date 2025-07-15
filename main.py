import os
import json
import fitz  # PyMuPDF
from pathlib import Path
from transformers import pipeline
import torch
from pydantic import BaseModel, Field
from typing import List

class TextElement:
    def __init__(self, text, font_size, x_position, y_position, page_width, page_height, 
                 is_bold=False, is_italic=False, width=0):
        self.text = text.strip()
        self.font_size = font_size
        self.x_position = x_position
        self.y_position = y_position
        self.page_width = page_width
        self.page_height = page_height
        self.is_bold = is_bold
        self.is_italic = is_italic
        self.width = width

class OutlineEntry(BaseModel):
    level: str = Field(..., pattern="^(H1|H2|H3)$")
    text: str
    page: int = Field(..., ge=1)

class DocumentStructure(BaseModel):
    title: str
    outline: List[OutlineEntry]

def extract_text_elements_from_pdf(pdf_path):
    """Extract text elements from PDF using PyMuPDF"""
    doc = fitz.open(pdf_path)
    pdf_pages = []
    
    for page_num in range(len(doc)):  # Process all pages
        page = doc[page_num]
        page_rect = page.rect
        text_dict = page.get_text("dict")
        text_elements = []
        
        for block in text_dict["blocks"]:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text or is_page_number(text):
                            continue
                            
                        font_size = span["size"]
                        font_flags = span["flags"]
                        is_bold = bool(font_flags & 2**4)
                        is_italic = bool(font_flags & 2**1)
                        bbox = span["bbox"]
                        x_position = bbox[0] / page_rect.width
                        y_position = 1 - (bbox[1] / page_rect.height)
                        width = (bbox[2] - bbox[0]) / page_rect.width
                        
                        if is_header_footer(y_position):
                            continue
                            
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
        
        page_obj = type('Page', (), {'text_elements': text_elements})()
        pdf_pages.append(page_obj)
    
    doc.close()
    return pdf_pages

def is_centered(text_element):
    """Check if text is roughly centered on the page"""
    text_center = text_element.x_position + (text_element.width / 2)
    page_center = 0.5
    return abs(text_center - page_center) < 0.1

def is_header_footer(y_position):
    """Check if text is in header/footer region"""
    return y_position > 0.95 or y_position < 0.05

def is_page_number(text):
    """Check if text looks like a page number"""
    return text.isdigit() or text.lower().startswith('page') or len(text) <= 3

def format_for_llm(pdf_pages):
    """Format text elements for LLM prompt"""
    prompt_lines = []
    for page_num, page in enumerate(pdf_pages):
        prompt_lines.append(f"Page {page_num + 1}:")
        for elem in page.text_elements:
            if elem.font_size < 10 or len(elem.text) < 5:  # Filter small or short text
                continue
            is_centered_val = is_centered(elem)
            prompt_lines.append(
                f"- Text: \"{elem.text}\", Font Size: {elem.font_size}, "
                f"Bold: {elem.is_bold}, Italic: {elem.is_italic}, Is Centered: {is_centered_val}"
            )
    return "\n".join(prompt_lines[:200])  # Limit to 200 lines to control token count

def extract_structure_with_llm(pdf_pages):
    """Use TinyLLaMA to extract title and headings"""
    # Load quantized TinyLLaMA model
    model_name = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"  # Replace with path to quantized model
    nlp = pipeline("text-generation", model=model_name, device=-1)  # CPU
    
    formatted_input = format_for_llm(pdf_pages)
    prompt = f"""
    Given a PDF document's text elements with metadata, extract the title and a hierarchical outline of headings (H1, H2, H3). Use font size, bold/italic formatting, and centering to infer structure. Output a JSON object in this format:
    {{
        "title": "<title>",
        "outline": [
            {{"level": "H1", "text": "<text>", "page": <page_num>}},
            ...
        ]
    }}

    Input:
    {formatted_input}

    Instructions:
    - Identify the title based on large font size (e.g., >=14), bold text, centered text, or top-page placement.
    - Identify H1, H2, H3 based on font size hierarchy (e.g., H1 > H2 > H3), bold formatting, or centering.
    - Assign page numbers from the input.
    - Exclude page numbers, headers/footers, or irrelevant short text.
    - Return valid JSON with the exact structure shown above.
    """
    
    try:
        result = nlp(prompt, max_length=1500, num_return_sequences=1, truncation=True)
        output_text = result[0]["generated_text"]
        
        # Extract JSON from output
        start_idx = output_text.find("{")
        end_idx = output_text.rfind("}") + 1
        if start_idx == -1 or end_idx == -1:
            raise ValueError("No valid JSON found in LLM output")
        json_str = output_text[start_idx:end_idx]
        
        # Validate with Pydantic
        data = json.loads(json_str)
        validated_data = DocumentStructure(**data)
        return validated_data.dict()
    except Exception as e:
        print(f"Error processing LLM output: {e}")
        return {"title": "Untitled Document", "outline": []}

def process_pdfs():
    input_dir = Path("/app/input")
    output_dir = Path("/app/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print("No PDF files found in /app/input")
        return
    
    for pdf_file in pdf_files:
        try:
            print(f"\n=== Processing {pdf_file.name} ===")
            pdf_pages = extract_text_elements_from_pdf(pdf_file)
            data = extract_structure_with_llm(pdf_pages)
            
            print(f"Extracted Title: '{data['title']}'")
            print(f"Outline: {data['outline']}")
            
            output_file = output_dir / f"{pdf_file.stem}.json"
            with open(output_file, "w") as f:
                json.dump(data, f, indent=2)
            print(f"✓ Created {output_file.name}")
            
        except Exception as e:
            print(f"✗ Error processing {pdf_file.name}: {e}")
            import traceback
            traceback.print_exc()
            fallback_data = {"title": "Untitled Document", "outline": []}
            output_file = output_dir / f"{pdf_file.stem}.json"
            with open(output_file, "w") as f:
                json.dump(fallback_data, f, indent=2)
            print(f"✓ Created fallback {output_file.name}")

if __name__ == "__main__":
    print("Starting processing PDFs")
    process_pdfs()
    print("Completed processing PDFs")