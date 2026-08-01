from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from .models import SourceFile


SUPPORTED = {".pdf", ".doc", ".docx"}
EXCLUDED_PARTS = {"预算", "保证金"}
PROJECT_SELF = "__project_self__"


def find_bidders(project_dir: Path) -> list[str]:
    """只把直接包含“投标文件”的一级目录视为投标单位。"""
    if (project_dir / "投标文件").is_dir():
        # 有些项目文件夹本身就是某个单位的交付目录，仍允许安全使用。
        return [PROJECT_SELF]
    return sorted(
        child.name
        for child in project_dir.iterdir()
        if child.is_dir() and (child / "投标文件").is_dir()
    )


def bid_root(project_dir: Path, bidder: str) -> Path:
    return project_dir / "投标文件" if bidder == PROJECT_SELF else project_dir / bidder / "投标文件"


def scan_bid_files(project_dir: Path, bidder: str) -> list[SourceFile]:
    root = bid_root(project_dir, bidder)
    if not root.is_dir():
        raise ValueError(f"找不到投标文件目录: {root}")
    candidates: list[SourceFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        candidates.append(SourceFile(path, str(relative), path.stem))
    # 软件只收 PDF。同名 PDF 已存在时，不能再把同一份 Word 当成第二个候选项。
    pdf_stems = {str(Path(item.relative_path).with_suffix("")) for item in candidates if item.path.suffix.lower() == ".pdf"}
    return [
        item for item in candidates
        if item.path.suffix.lower() == ".pdf" or str(Path(item.relative_path).with_suffix("")) not in pdf_stems
    ]


def normalize(value: str) -> str:
    value = value.lower().replace("（", "(").replace("）", ")")
    value = re.sub(r"^[a-z]\s*[-_.、]", "", value)
    value = re.sub(r"^(?:\d+|[一二三四五六七八九十百]+)[、.\-]", "", value)
    value = re.sub(r"[\s_\-—–·,.，、()（）【】\[\]]+", "", value)
    value = re.sub(r"(?:若有|如有)$", "", value)
    return value


def score_match(chapter_title: str, source: SourceFile) -> tuple[int, str]:
    """仅根据实际章节标题与文件名打分，不含单位或项目硬编码。"""
    chapter = normalize(chapter_title)
    name = normalize(source.display_name)
    if not chapter or not name:
        return 0, ""
    if chapter == name:
        return 100, "章节名与文件名一致"
    if chapter in name or name in chapter:
        return min(90, 50 + min(len(chapter), len(name))), "章节名与文件名包含关系"
    chapter_tokens = {t for t in re.split(r"[^\w\u4e00-\u9fff]+", chapter_title) if len(t) > 1}
    name_tokens = {t for t in re.split(r"[^\w\u4e00-\u9fff]+", source.display_name) if len(t) > 1}
    overlap = chapter_tokens & name_tokens
    if overlap:
        return 30 + 10 * len(overlap), "部分词语重合"
    return 0, ""


def prepare_pdf(source: SourceFile, output_dir: Path) -> Path:
    """只在本工具 output 中创建副本或转换文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", source.relative_path)
    destination = output_dir / f"{uuid4().hex[:8]}_{Path(safe).stem}.pdf"
    if source.path.suffix.lower() == ".pdf":
        shutil.copy2(source.path, destination)
        return destination
    try:
        import pythoncom
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("需要 pywin32 和 Microsoft Word 才能转换 Word") from exc
    pythoncom.CoInitialize()
    word = document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(source.path.resolve()), ReadOnly=True)
        document.SaveAs(str(destination.resolve()), FileFormat=17)  # wdFormatPDF
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    if not destination.exists():
        raise RuntimeError(f"Word 转 PDF 失败: {source.path.name}")
    return destination
