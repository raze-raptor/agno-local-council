"""Turn an answer (markdown) into a real .docx with the standard library only (a .docx is a zip of small XML parts). Headings, bullets, numbered lines, bold, code and
paragraphs are kept; anything fancier becomes plain text."""
import io
import re
import zipfile
from xml.sax.saxutils import escape


def _runs(t):
    out = []
    for i, part in enumerate(re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", t)):
        if not part: continue
        if part.startswith("**"): out.append(f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{escape(part[2:-2])}</w:t></w:r>')
        elif part.startswith("`"): out.append(f'<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr><w:t xml:space="preserve">{escape(part[1:-1])}</w:t></w:r>')
        else: out.append(f'<w:r><w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def _p(t, style=None, ind=0):
    pp = (f'<w:pStyle w:val="{style}"/>' if style else "") + (f'<w:ind w:left="{ind}" w:hanging="260"/>' if ind else "")
    return f'<w:p>{"<w:pPr>" + pp + "</w:pPr>" if pp else ""}{_runs(t)}</w:p>'


def docx_bytes(title, md):
    body, code = [_p(title, "Title")], False
    for ln in md.splitlines():
        if ln.strip().startswith("```"): code = not code; continue
        if code: body.append(f'<w:p><w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr><w:t xml:space="preserve">{escape(ln)}</w:t></w:r></w:p>'); continue
        m = re.match(r"^(#{1,3})\s+(.*)", ln)
        if m: body.append(_p(m.group(2), f"Heading{len(m.group(1))}")); continue
        m = re.match(r"^\s*[-*]\s+(.*)", ln)
        if m: body.append(_p("• " + m.group(1), ind=360)); continue
        m = re.match(r"^\s*(\d+)[.)]\s+(.*)", ln)
        if m: body.append(_p(m.group(1) + ". " + m.group(2), ind=360)); continue
        if ln.strip(): body.append(_p(ln.strip()))
    doc = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + "".join(body) + "</w:body></w:document>"
    sty = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' + "".join(
        f'<w:style w:type="paragraph" w:styleId="{i}"><w:name w:val="{n}"/><w:rPr><w:b/><w:sz w:val="{s}"/></w:rPr></w:style>' for i, n, s in (("Title", "Title", 40), ("Heading1", "heading 1", 32), ("Heading2", "heading 2", 28), ("Heading3", "heading 3", 24))) + "</w:styles>"
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("word/document.xml", doc); z.writestr("word/styles.xml", sty)
    return b.getvalue()
