#!/usr/bin/env python3
"""
linux
sudo apt-get install xclip

win
pip install pywin32

Cross-platform clipboard saver for rich text (HTML).
check clipboard data type. Reads formatted text from clipboard and saves to a timestamped file. So if i copy from web browser - it will save not a text but html formatting. It should work as on site justpaste.it
Works on Windows and Linux.

File naming format: {site_name}_{YYYY.MM.DD.HH.MM.SS}.htm
"""



import subprocess
from datetime import datetime
import os
import sys
import re


def get_clipboard_rich_text_windows():
    """Get rich text from clipboard on Windows."""
    try:
        import win32clipboard
        
        win32clipboard.OpenClipboard()
        
        # HTML Format constant (standard Windows clipboard format)
        CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
        
        try:
            # Try to get HTML format
            html_data = win32clipboard.GetClipboardData(CF_HTML)
            win32clipboard.CloseClipboard()
            
            # HTML format includes header, extract just the HTML part
            if isinstance(html_data, bytes):
                html_data = html_data.decode('utf-8', errors='ignore')
            
            # Remove HTML Format header if present
            if "<!--StartFragment-->" in html_data:
                start = html_data.find("<!--StartFragment-->") + len("<!--StartFragment-->")
                end = html_data.find("<!--EndFragment-->")
                return html_data[start:end].strip()
            
            return html_data
            
        except TypeError:
            # Fall back to plain text
            plain_text = win32clipboard.GetClipboardData()
            win32clipboard.CloseClipboard()
            return plain_text
            
    except ImportError:
        print("Error: pywin32 not installed. Run: pip install pywin32", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Windows clipboard error: {e}", file=sys.stderr)
        return None


def get_clipboard_rich_text_linux():
    """Get rich text from clipboard on Linux."""
    try:
        # Try xclip with HTML MIME type
        result = subprocess.run(
            ['xclip', '-selection', 'clipboard', '-t', 'text/html', '-o'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        
        # Fall back to plain text
        result = subprocess.run(
            ['xclip', '-selection', 'clipboard', '-o'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            return result.stdout
        
        return None
    except FileNotFoundError:
        print("Error: xclip not installed. Run: sudo apt-get install xclip", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Linux clipboard error: {e}", file=sys.stderr)
        return None


def get_clipboard_content():
    """Get text from clipboard (platform-independent)."""
    system = sys.platform
    
    if 'win32' in system or 'cygwin' in system:
        content = get_clipboard_rich_text_windows()
    elif 'linux' in system or 'darwin' in system:
        content = get_clipboard_rich_text_linux()
    else:
        print(f"Unsupported system: {system}", file=sys.stderr)
        sys.exit(1)
    
    return content


def extract_site_name(html_content):
    """Extract site name from SourceURL in HTML content."""
    # Search for SourceURL in HTML
    match = re.search(r'SourceURL:\s*(https?://[^\s]+)', html_content)
    if match:
        url = match.group(1)
        # Remove http:// https://
        site_name = re.sub(r'^https?://', '', url)
        # Get domain only (remove path)
        site_name = site_name.split('/')[0]
        # Remove www. prefix and limit to 15 characters
        site_name = site_name.replace('www.', '')
        return site_name[:15]
    return "unknown_site"


def create_filename(html_content):
    """Create timestamped filename: {site_name}_{YYYY.MM.DD.HH.MM.SS}.htm"""
    now = datetime.now()
    timestamp = now.strftime("%Y.%m.%d.%H.%M.%S")
    site_name = extract_site_name(html_content)
    filename = f"{site_name}_{timestamp}.htm"
    return filename


def save_to_file(content, filename):
    """Save clipboard content to file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Clipboard saved to: {os.path.abspath(filename)}")
        print(f"  File size: {len(content)} characters")
    except Exception as e:
        print(f"Error writing to file: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main function."""
    content = get_clipboard_content()
    
    if not content:
        print("Error: Clipboard is empty or cannot read rich text!", file=sys.stderr)
        sys.exit(1)
    
    filename = create_filename(content)
    save_to_file(content, filename)


if __name__ == "__main__":
    main()
