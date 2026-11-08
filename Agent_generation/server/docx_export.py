"""结构化合同 → Word（.docx）

- 标题居中，首部/尾部按行分段；章节为 Heading 2，条款/子条款按层级缩进（编号保留）
- 中文字体：宋体（正文）/ 黑体（标题），A4 页边距 2.54cm，1.5 倍行距，页脚页码
- 大纲阶段导出时，条款为“大致说明”
- 与网页预览一致：章节标题居中；条款内 Markdown 表格导出为 Word 表格；尾部甲乙方签章并排为两栏（无边框表格）
"""
import io
import re
from typing import List, Optional, Tuple

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


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEP_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_PARTY = re.compile(r"^(甲方|乙方|丙方|丁方|出租方|承租方|买方|卖方|委托方|受托方|服务方|授权方|被授权方|出借人|借款人|"
                    r"发包人|承包人|转让方|受让方|基金管理人|基金托管人|用人单位|劳动者)")
_SIGN = re.compile(r"(签字|签章|盖章|签署日期|授权代表)")


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _split_rich(text: str) -> List[Tuple[str, object]]:
    """条款文本 → [("text", 行) | ("table", (表头, 行列表))]，识别 Markdown 表格"""
    lines = str(text or "").split("\n")
    out, i = [], 0
    while i < len(lines):
        if _TABLE_ROW.match(lines[i]) and i + 1 < len(lines) and _SEP_ROW.match(lines[i + 1]):
            head, rows, i = _cells(lines[i]), [], i + 2
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                rows.append(_cells(lines[i]))
                i += 1
            out.append(("table", (head, rows)))
            continue
        if lines[i].strip():
            out.append(("text", lines[i].strip()))
        i += 1
    return out


def _table(doc, head: List[str], rows: List[List[str]]):
    n = max([len(head)] + [len(r) for r in rows])
    t = doc.add_table(rows=1 + len(rows), cols=n)
    t.style = "Table Grid"
    for r_idx, row in enumerate([head] + rows):
        for c_idx in range(n):
            cell = t.cell(r_idx, c_idx)
            cell.text = ""
            run = cell.paragraphs[0].add_run(row[c_idx] if c_idx < len(row) else "")
            _font(run, "宋体", 10.5, bold=(r_idx == 0))
            if r_idx == 0:
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _clause(doc, num: str, text: str, indent_cm: float, first_line_cm: float):
    parts = _split_rich(text)
    if not parts:
        _para(doc, num, indent_cm=indent_cm, first_line_cm=first_line_cm)
        return
    for k, (kind, val) in enumerate(parts):
        if kind == "table":
            if k == 0:
                _para(doc, num, indent_cm=indent_cm, first_line_cm=first_line_cm)
            _table(doc, *val)
        else:
            _para(doc, f"{num} {val}".strip() if k == 0 else val, indent_cm=indent_cm, first_line_cm=first_line_cm)


def _no_border(table):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def _footer(doc, text: str):
    """尾部：连续的两组“甲方…签字/盖章”并排成两栏，其余按段落输出"""
    groups, cur = [], []
    for raw in str(text or "").split("\n"):
        ln = raw.strip()
        if not ln:
            if cur:
                groups.append(cur)
            cur = []
        else:
            cur.append(ln)
    if cur:
        groups.append(cur)
    is_sign = lambda g: bool(_PARTY.match(g[0])) and any(_SIGN.search(x) for x in g)
    i = 0
    while i < len(groups):
        g = groups[i]
        if is_sign(g) and i + 1 < len(groups) and is_sign(groups[i + 1]):
            cols = [g, groups[i + 1]]
            t = doc.add_table(rows=1, cols=2)
            _no_border(t)
            for c_idx, col in enumerate(cols):
                cell = t.cell(0, c_idx)
                cell.text = ""
                for k, ln in enumerate(col):
                    p = cell.paragraphs[0] if k == 0 else cell.add_paragraph()
                    p.paragraph_format.space_after = Pt(6)
                    _font(p.add_run(ln), "宋体", 11, bold=(k == 0))
            doc.add_paragraph()
            i += 2
            continue
        for ln in g:
            if re.fullmatch(r"[（(]?以下无正文[）)]?", ln):
                _para(doc, ln, align=WD_ALIGN_PARAGRAPH.CENTER)
            else:
                _para(doc, ln, size=11)
        i += 1


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
    title = re.sub(r"^(协议名称|合同名称)[：:]\s*", "", title).strip()
    title = re.sub(r"^《(.+)》$", r"\1", title)
    if len(title) > 40:
        title = title_hint or "合同"
    else:
        header_lines = header_lines[1:]
    _para(doc, title, size=18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, font="黑体", space_after=12)
    for ln in header_lines:
        _para(doc, ln, size=11)

    for s in tpl.get("正文章节", []) or []:
        head = f"{s.get('章节编号', '')} {s.get('章节标题', '')}".strip()
        hp = _para(doc, head, size=12.5, bold=True, font="黑体", space_after=6, align=WD_ALIGN_PARAGRAPH.CENTER)
        hp.paragraph_format.space_before = Pt(10)
        for c in s.get("条款列表", []) or []:
            num = str(c.get("条款编号") or c.get("locator") or "")
            _clause(doc, num, c.get("条款大致说明", ""), 0.0, 0.74)
            for sc in c.get("子条款列表", []) or []:
                n2 = str(sc.get("子条款编号") or sc.get("locator") or "")
                _clause(doc, n2, sc.get("子条款大致说明", ""), 0.74, 0.0)
                for ssc in sc.get("子子条款列表", []) or []:
                    n3 = str(ssc.get("子子条款编号") or ssc.get("locator") or "")
                    _clause(doc, n3, ssc.get("子子条款大致说明", ""), 1.48, 0.0)

    footer_text = str(tpl.get("合同尾部", "") or "")
    if footer_text.strip():
        doc.add_paragraph()
        _footer(doc, footer_text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name or "合同").strip() or "合同"
    return name[:60]
