from __future__ import annotations

import re
from pathlib import Path

from .members import validate_members


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _find_pdf(root: Path, keyword: str) -> Path:
    matches = sorted(
        path for path in root.rglob("*.pdf")
        if keyword in _compact(path.stem)
    )
    if not matches:
        raise ValueError(f"项目投标文件中未找到“{keyword}”PDF")
    if len(matches) > 1:
        exact = [path for path in matches if _compact(path.stem).endswith(keyword)]
        if len(exact) == 1:
            return exact[0]
        raise ValueError(f"找到多份“{keyword}”PDF，无法安全自动选择")
    return matches[0]


def _member_type(role: str) -> str:
    if "项目负责人" in role or "项目经理" in role or "总监" in role:
        return "1"
    if "技术负责人" in role:
        return "3"
    if "安全" in role:
        return "4"
    return "9"


def extract_project_members(bid_files_root: Path) -> tuple[list[dict[str, str]], list[Path]]:
    """从成品 PDF 的真实表格中合并成员身份与证书信息。

    只读取 PDF，不写入编制工具。用户必须在网页预览后再单独确认写入。
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("自动识别成员 PDF 需要 pdfplumber") from exc

    member_pdf = _find_pdf(bid_files_root, "项目班子成员信息表")
    roster_pdf = _find_pdf(bid_files_root, "项目管理班子配备情况表")

    base_rows: list[dict[str, str]] = []
    with pdfplumber.open(member_pdf) as document:
        for page in document.pages:
            for table in page.extract_tables() or []:
                if not table or not table[0]:
                    continue
                header = [_compact(cell) for cell in table[0]]
                if not ({"岗位", "姓名", "身份证号码"} <= set(header)):
                    continue
                role_index = header.index("岗位")
                name_index = header.index("姓名")
                id_index = header.index("身份证号码")
                for row in table[1:]:
                    if not row or max(role_index, name_index, id_index) >= len(row):
                        continue
                    role = _compact(row[role_index])
                    name = _compact(row[name_index])
                    cert_no = _compact(row[id_index]).upper()
                    if not role or not name or not re.fullmatch(r"\d{17}[\dX]", cert_no):
                        continue
                    base_rows.append({
                        "role": role,
                        "bid_member_name": name,
                        "bid_member_type": _member_type(role),
                        "bid_member_cert_no": cert_no,
                    })
                if base_rows:
                    break
            if base_rows:
                break
    if not base_rows:
        raise ValueError("成员信息表 PDF 中未识别到有效姓名和身份证号")

    certificates: dict[str, tuple[str, str]] = {}
    with pdfplumber.open(roster_pdf) as document:
        for page in document.pages:
            for table in page.extract_tables() or []:
                if len(table) < 3 or len(table[0] or []) < 6:
                    continue
                first_two = "".join(_compact(cell) for row in table[:2] for cell in (row or []))
                if "证书名称" not in first_two or "证号" not in first_two:
                    continue
                for row in table[2:]:
                    if not row or len(row) < 6:
                        continue
                    name = _compact(row[1])
                    certificate_name = _compact(row[3])
                    certificate_num = _compact(row[5])
                    if name and certificate_name and certificate_name != "/" and certificate_num and certificate_num != "/":
                        certificates[name] = (certificate_name, certificate_num)
                if certificates:
                    break
            if certificates:
                break

    members: list[dict[str, str]] = []
    for row in base_rows:
        certificate_name, certificate_num = certificates.get(row["bid_member_name"], ("", ""))
        members.append({
            "bid_member_name": row["bid_member_name"],
            "bid_member_type": row["bid_member_type"],
            "bid_member_cert_no": row["bid_member_cert_no"],
            "bid_member_certificate_name": certificate_name,
            "bid_member_certificate_num": certificate_num,
        })
    validate_members(members)
    return members, [member_pdf, roster_pdf]
