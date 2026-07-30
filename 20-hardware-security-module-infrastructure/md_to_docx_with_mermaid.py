#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Convert Markdown containing Mermaid diagrams into a Word DOCX file.
Extracts Mermaid blocks, renders them to PNG, and builds the final document.
"""

import re
import subprocess
import tempfile
import os
from pathlib import Path

def extract_mermaid_blocks(md_content):
    """Extract every Mermaid block from the Markdown source."""
    pattern = r'```mermaid\n(.*?)```'
    blocks = re.findall(pattern, md_content, re.DOTALL)
    return blocks

def convert_mermaid_to_png(mermaid_code, output_path):
    """Render Mermaid source to a PNG file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False) as f:
        f.write(mermaid_code)
        temp_mmd = f.name
    
    try:
        # Use mermaid-cli to render the diagram
        subprocess.run([
            'mmdc',
            '-i', temp_mmd,
            '-o', output_path,
            '-t', 'default',
            '-b', 'white'
        ], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error rendering Mermaid diagram: {e}")
        return False
    except FileNotFoundError:
        print("ERROR: mermaid-cli (mmdc) not found.")
        print("Install it with: npm install -g @mermaid-js/mermaid-cli")
        return False
    finally:
        os.unlink(temp_mmd)

def replace_mermaid_with_images(md_content, image_dir='images'):
    """Replace Mermaid blocks with image references."""
    Path(image_dir).mkdir(exist_ok=True)
    
    pattern = r'```mermaid\n(.*?)```'
    counter = 0
    
    def replacer(match):
        nonlocal counter
        counter += 1
        mermaid_code = match.group(1)
        image_name = f'diagram_{counter}.png'
        image_path = f'{image_dir}/{image_name}'
        
        # Render to PNG
        if convert_mermaid_to_png(mermaid_code, image_path):
            # Return an image reference in Markdown
            return f'\n![Diagram {counter}]({image_path})\n'
        else:
            # On failure, keep the original code block
            return match.group(0)
    
    return re.sub(pattern, replacer, md_content, flags=re.DOTALL)

def convert_to_docx(input_md, output_docx, template=None):
    """Convert Markdown to DOCX using Pandoc."""
    cmd = [
        'pandoc',
        input_md,
        '-o', output_docx,
        '--toc',
        '--number-sections',
        '--highlight-style=tango',
        '--standalone',
        '--resource-path=.'
    ]
    
    if template and os.path.exists(template):
        cmd.extend(['--reference-doc', template])
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"✅ Document created: {output_docx}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error converting to DOCX: {e.stderr.decode()}")
        return False

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Convert Markdown with Mermaid diagrams to DOCX')
    parser.add_argument('input', help='Input Markdown file')
    parser.add_argument('-o', '--output', help='Output DOCX file', default='output.docx')
    parser.add_argument('-t', '--template', help='Reference DOCX template')
    parser.add_argument('--keep-temp', action='store_true', help='Keep the temporary Markdown file')
    
    args = parser.parse_args()
    
    # Read the input file
    print(f"📖 Reading {args.input}...")
    with open(args.input, 'r', encoding='utf-8') as f:
        md_content = f.read()
    
    # Extract and count Mermaid blocks
    mermaid_blocks = extract_mermaid_blocks(md_content)
    print(f"🔍 Found {len(mermaid_blocks)} Mermaid diagrams")
    
    # Replace Mermaid blocks with images
    print("🎨 Rendering diagrams to PNG...")
    md_with_images = replace_mermaid_with_images(md_content)
    
    # Write the temporary Markdown file
    temp_md = 'temp_with_images.md'
    with open(temp_md, 'w', encoding='utf-8') as f:
        f.write(md_with_images)
    
    print(f"📄 Temporary Markdown created: {temp_md}")
    
    # Convert to DOCX
    print(f"📝 Converting to {args.output}...")
    success = convert_to_docx(temp_md, args.output, args.template)
    
    # Clean up temporary files
    if not args.keep_temp:
        os.unlink(temp_md)
    
    if success:
        print(f"\n✨ Conversion complete. File: {args.output}")
        print(f"📊 Diagrams rendered: {len(mermaid_blocks)}")
    else:
        print("\n⚠️  Conversion finished with problems. Check the logs above.")

if __name__ == '__main__':
    main()