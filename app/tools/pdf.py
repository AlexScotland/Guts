from langchain_core.tools import tool
import pypdf

@tool
def get_pdf_info(file_path: str) -> str:
    """
    Returns the total number of pages in a specified PDF file.
    Use this tool FIRST to understand how large the document is.
    """
    try:
        reader = pypdf.PdfReader(file_path)
        total_pages = len(reader.pages)
        return f"The file '{file_path}' has a total of {total_pages} pages."
    except FileNotFoundError:
        return f"Error: The file at {file_path} was not found."
    except Exception as e:
        return f"An error occurred: {str(e)}"


@tool
def read_pdf_page(file_path: str, page_number: int) -> str:
    """
    Reads all text content from one single, specific page of a PDF file.
    The 'page_number' argument is 1-indexed (e.g., page 1 is the first page).
    """
    try:
        reader = pypdf.PdfReader(file_path)
        total_pages = len(reader.pages)
        
        # Validate page number bounds
        if page_number < 1 or page_number > total_pages:
            return f"Error: Requested page {page_number} is out of bounds. The document only has {total_pages} pages."
            
        # Convert 1-indexed user input to 0-indexed python list item
        page_index = page_number - 1
        page = reader.pages[page_index]
        text = page.extract_text()
        
        if not text or not text.strip():
            return f"--- Page {page_number} of {total_pages} ---\n[Warning: This page appears to be blank or contains only images.]"
            
        return f"--- Page {page_number} of {total_pages} ---\n{text}"
        
    except FileNotFoundError:
        return f"Error: The file at {file_path} was not found."
    except Exception as e:
        return f"An error occurred: {str(e)}"
