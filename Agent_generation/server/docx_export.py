"""结构化合同 → Word（.docx）

- 标题居中，首部/尾部按行分段；章节为 Heading 2，条款/子条款按层级缩进（编号保留）
- 中文字体：宋体（正文）/ 黑体（标题），A4 页边距 2.54cm，1.5 倍行距，页脚页码
- 大纲阶段导出时，条款为“大致说明”
"""
import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


def _font(run, name="宋体", size=11, bold=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color:
        run.font.color.rgb = RGBColor(*color)


def _para(doc, text, size=11, bold=False, indent_cm=0.0, first_line_cm=0.0, align=None, font="宋体",
          space_after=4):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = Cm(indent_cm)
    pf.first_line_indent = Cm(first_line_cm) if first_line_cm else None
    pf.space_after = Pt(space_after)
    pf.line_spacing = 1.5
    if align is not None:
        p.alignment = align
    _font(p.add_run(text), font, size, bold)
    return p


def _page_number(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = txt
        run._r.append(el)
    _font(run, "宋体", 9)


def contract_to_docx(contract: dict, title_hint: str = "") -> bytes:
    tpl = (contract or {}).get("标准化模板文本", contract or {})
    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(2.54))
    _page_number(sec)
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    header_lines = [ln.strip() for ln in str(tpl.get("合同首部", "")).splitlines() if ln.strip()]
    title = header_lines[0] if header_lines else (title_hint or "合同")
    if len(title) > 40:
        title = title_hint or "合同"
    else:
        header_lines = header_lines[1:]
    _para(doc, title, size=18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, font="黑体", space_after=12)
    for ln in header_lines:
        _para(doc, ln, size=11)

    for s in tpl.get("正文章节", []) or []:
        head = f"{s.get('章节编号', '')} {s.get('章节标题', '')}".strip()
        _para(doc, head, size=12.5, bold=True, font="黑体", space_after=6).paragraph_format.space_before = Pt(10)
        for c in s.get("条款列表", []) or []:
            num = str(c.get("条款编号") or c.get("locator") or "")
            _para(doc, f"{num} {c.get('条款大致说明', '')}".strip(), indent_cm=0.0, first_line_cm=0.74)
            for sc in c.get("子条款列表", []) or []:
                n2 = str(sc.get("子条款编号") or sc.get("locator") or "")
                _para(doc, f"{n2} {sc.get('子条款大致说明', '')}".strip(), indent_cm=0.74)
                for ssc in sc.get("子子条款列表", []) or []:
                    n3 = str(ssc.get("子子条款编号") or ssc.get("locator") or "")
                    _para(doc, f"{n3} {ssc.get('子子条款大致说明', '')}".strip(), indent_cm=1.48)

    footer = [ln.strip() for ln in str(tpl.get("合同尾部", "")).splitlines() if ln.strip()]
    if footer:
        doc.add_paragraph()
        for ln in footer:
            _para(doc, ln, size=11)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name or "合同").strip() or "合同"
    return name[:60]
