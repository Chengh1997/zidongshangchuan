from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _unique_pdf(root: Path, keyword: str) -> Path:
    matches = sorted(path for path in root.rglob("*.pdf") if keyword in _compact(path.stem))
    exact = [path for path in matches if _compact(path.stem).endswith(keyword)]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"项目投标文件中未找到“{keyword}”PDF")
    raise ValueError(f"找到多份“{keyword}”PDF，无法安全自动选择")


def _value_after(row: list[object], label: str) -> str:
    cells = [_compact(cell) for cell in row]
    for index, cell in enumerate(cells):
        if label in cell:
            for candidate in cells[index + 1:]:
                if candidate:
                    return candidate
    return ""


def _age(identity: str, today: date | None = None) -> int:
    current = today or date.today()
    born = date(int(identity[6:10]), int(identity[10:12]), int(identity[12:14]))
    return current.year - born.year - ((current.month, current.day) < (born.month, born.day))


def extract_bidder_profile(bid_files_root: Path) -> tuple[dict[str, object], dict[str, str], Path]:
    """从投标人基本情况表提取企业、法人、联系人及委托代理人资料。"""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("自动识别投标人资料需要 pdfplumber") from exc

    source = _unique_pdf(bid_files_root, "投标人基本情况表")
    rows: list[list[object]] = []
    with pdfplumber.open(source) as document:
        for page in document.pages:
            for table in page.extract_tables() or []:
                if table and any("投标人名称" in _compact(cell) for cell in table[0] or []):
                    rows = table
                    break
            if rows:
                break
    if not rows:
        raise ValueError("投标人基本情况表中未识别到资料表格")

    company = contact = phone = address = postal = legal_name = uscc = ""
    legal_id = agent_name = agent_id = agent_phone = ""
    pending_identity = ""
    for row in rows:
        cells = [_compact(cell) for cell in row]
        joined = "|".join(cells)
        first = next((cell for cell in cells if cell), "")
        if first == "投标人名称":
            company = _value_after(row, "投标人名称")
        elif first == "联系人":
            contact = _value_after(row, "联系人")
            phone = _value_after(row, "电话")
        elif first == "注册地址":
            address = _value_after(row, "注册地址")
            postal = _value_after(row, "邮政编码")
        elif "法定代表人姓名" in first:
            legal_name = _value_after(row, "法定代表人姓名")
        elif first == "营业执照号":
            uscc = _value_after(row, "营业执照号").upper()

        if "投标直接责任人员为本次投标委托授权代表" in joined:
            marker = next(i for i, cell in enumerate(cells) if "委托授权代表" in cell)
            agent_name = next((cell for cell in cells[marker + 1:] if cell and cell != "电话" and not re.fullmatch(r"1\d{10}", cell)), agent_name)
            agent_phone = _value_after(row, "电话")
            pending_identity = "agent"
        elif "投标的主管人员为法定代表人" in joined:
            marker = next(i for i, cell in enumerate(cells) if "法定代表人" in cell)
            legal_name = next((cell for cell in cells[marker + 1:] if cell and cell != "电话" and not re.fullmatch(r"1\d{10}", cell)), legal_name)
            pending_identity = "legal"
        elif "身份证号" in joined:
            identity = next((cell.upper() for cell in cells if re.fullmatch(r"\d{17}[\dX]", cell.upper())), "")
            if pending_identity == "agent":
                agent_id = identity
            elif pending_identity == "legal":
                legal_id = identity

    required = {"投标人名称": company, "统一信用代码": uscc, "法定代表人": legal_name, "法人身份证号": legal_id, "地址": address, "邮编": postal, "联系人": contact, "联系方式": phone}
    missing = [label for label, value in required.items() if not value]
    if missing:
        raise ValueError("投标人基本情况表缺少：" + "、".join(missing))
    if not re.fullmatch(r"[A-HJ-NP-Z0-9]{18}", uscc):
        raise ValueError("识别到的统一社会信用代码格式错误")
    if not re.fullmatch(r"\d{17}[\dX]", legal_id):
        raise ValueError("识别到的法定代表人身份证号格式错误")
    if not re.fullmatch(r"1\d{10}", phone) or not re.fullmatch(r"\d{6}", postal):
        raise ValueError("识别到的联系方式或邮编格式错误")

    profile: dict[str, object] = {
        "name": company,
        "uscc": uscc,
        "legalPersonName": legal_name,
        "legalPersonSex": "男" if int(legal_id[16]) % 2 else "女",
        "legalPersonIdCard": legal_id,
        "legalPersonAge": _age(legal_id),
        "legalPersonDuty": "法定代表人",
        "address": address,
        "postalCode": postal,
        "contactPerson": contact,
        "phone": phone,
    }
    agent = {"agentPerson": agent_name, "agentPersonIdCard": agent_id, "agentPhone": agent_phone}
    return profile, agent, source
