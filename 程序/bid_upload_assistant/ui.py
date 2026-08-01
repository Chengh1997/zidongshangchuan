from __future__ import annotations

import tkinter as tk
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .controller import UploadController
from .files import PROJECT_SELF, bid_root, find_bidders
from .gateway import GatewayError


class App(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=14)
        self.root = root
        self.workspace = Path(__file__).resolve().parents[1]
        self.controller = UploadController(self.workspace)
        self.project_var = tk.StringVar()
        self.bidder_var = tk.StringVar()
        self.zjzbs_var = tk.StringVar()
        self.endpoint_var = tk.StringVar(value="http://127.0.0.1:9222")
        self.exe_var = tk.StringVar(value=r"C:\Program Files\投标文件编制工具\tenderBidApp.exe")
        self.status_var = tk.StringVar(value="请选择测试项目、单位和 .zjzbs。")
        self.member_var = tk.StringVar(value="成员资料：尚未加载")
        self._build()

    def _build(self) -> None:
        self.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="投标章节上传助手", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(self, text="先读取真实章节树；单文件核验成功前，批量上传始终锁定。", foreground="#9c6500").grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 12))

        self._field(2, "项目文件夹", self.project_var, self.choose_project)
        ttk.Label(self, text="投标单位").grid(row=3, column=0, sticky="w", pady=4)
        self.bidder_box = ttk.Combobox(self, textvariable=self.bidder_var, state="readonly")
        self.bidder_box.grid(row=3, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(self, text="重新识别单位", command=self.refresh_bidders).grid(row=3, column=3, sticky="ew")
        self._field(4, ".zjzbs 招标文件", self.zjzbs_var, self.choose_zjzbs, ("招标文件", "*.zjzbs"))
        ttk.Label(self, text="CEF 调试地址").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.endpoint_var).grid(row=5, column=1, columnspan=3, sticky="ew", padx=8)
        ttk.Label(self, text="编制工具路径").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.exe_var).grid(row=6, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(self, text="以调试模式启动软件", command=self.launch_tool).grid(row=6, column=3, sticky="ew")

        members = ttk.LabelFrame(self, text="成员信息（对应软件中的“成员信息”表）", padding=8)
        members.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Button(members, text="导出成员表模板", command=self.export_member_template).pack(side="left")
        ttk.Button(members, text="加载成员 CSV/Excel", command=self.load_member_sheet).pack(side="left", padx=8)
        ttk.Button(members, text="预览成员", command=self.preview_members).pack(side="left")
        ttk.Button(members, text="写入软件并重新核验", command=self.write_members).pack(side="left", padx=8)
        ttk.Label(members, textvariable=self.member_var).pack(side="left", padx=8)

        actions = ttk.Frame(self)
        actions.grid(row=8, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Button(actions, text="连接软件并导入/读取章节", command=self.read_chapters).pack(side="left")
        ttk.Button(actions, text="为选中章节人工指定文件", command=self.pick_manual_source).pack(side="left", padx=8)
        ttk.Button(actions, text="单文件验证", command=self.verify_one).pack(side="left")
        self.batch_button = ttk.Button(actions, text="批量上传（需先单文件核验）", command=self.run_batch, state="disabled")
        self.batch_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop_batch, state="disabled")
        self.stop_button.pack(side="left")

        columns = ("chapter", "file", "confidence", "status", "reason")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=17)
        labels = {"chapter": "软件真实章节", "file": "对应文件", "confidence": "匹配", "status": "状态", "reason": "说明"}
        widths = {"chapter": 250, "file": 330, "confidence": 70, "status": 80, "reason": 300}
        for key in columns:
            self.tree.heading(key, text=labels[key])
            self.tree.column(key, width=widths[key], anchor="w")
        self.tree.grid(row=9, column=0, columnspan=4, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=9, column=4, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.rowconfigure(9, weight=1)
        ttk.Label(self, textvariable=self.status_var, wraplength=1000).grid(row=10, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _field(self, row: int, title: str, variable: tk.StringVar, command, filetype=None) -> None:
        ttk.Label(self, text=title).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(self, text="选择", command=command).grid(row=row, column=3, sticky="ew")

    def choose_project(self) -> None:
        selected = filedialog.askdirectory(title="选择项目文件夹")
        if selected:
            self.project_var.set(selected)
            self.refresh_bidders()

    def refresh_bidders(self) -> None:
        try:
            bidders = find_bidders(Path(self.project_var.get()))
        except Exception as exc:
            messagebox.showerror("无法识别单位", str(exc))
            return
        display = ["当前项目（单单位结构）" if bidder == PROJECT_SELF else bidder for bidder in bidders]
        self.bidder_box["values"] = display
        if bidders:
            self.bidder_var.set(display[0])
        self.status_var.set(f"识别到 {len(bidders)} 个单位。")

    def choose_zjzbs(self) -> None:
        selected = filedialog.askopenfilename(title="选择 .zjzbs", filetypes=[("ZJZBS", "*.zjzbs")])
        if selected:
            self.zjzbs_var.set(selected)

    def launch_tool(self) -> None:
        try:
            self.controller.launch_tool(self.exe_var.get())
            self.status_var.set("已请求以调试模式启动编制工具。请完成软件启动后，再点击“连接软件并导入/读取章节”。")
        except Exception as exc:
            messagebox.showerror("无法启动", str(exc))

    def export_member_template(self) -> None:
        path = self.controller.export_member_template()
        self.status_var.set(f"成员表模板已输出到：{path}")
        messagebox.showinfo("模板已创建", "请在模板中填写真实、已核对的成员资料。成员类型可写：项目负责人/总监、技术负责人、安全负责人、其他。")

    def load_member_sheet(self) -> None:
        selected = filedialog.askopenfilename(title="选择成员资料表", filetypes=[("成员资料", "*.csv *.xlsx *.xls")])
        if not selected:
            return
        try:
            members = self.controller.load_member_sheet(selected)
            self.member_var.set(f"成员资料：已加载 {len(members)} 人")
            self.preview_members()
        except Exception as exc:
            messagebox.showerror("成员资料不合格", str(exc))

    def preview_members(self) -> None:
        if not self.controller.members:
            messagebox.showwarning("没有成员资料", "请先加载成员 CSV 或 Excel。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("成员资料预览（写入前请核对）")
        columns = ("name", "type", "id", "qualification", "number")
        table = ttk.Treeview(dialog, columns=columns, show="headings", height=min(12, len(self.controller.members)))
        labels = ("姓名", "类型代码", "身份证号", "职业资格名称", "资格证书编号")
        for key, label in zip(columns, labels):
            table.heading(key, text=label)
            table.column(key, width=170)
        for member in self.controller.members:
            table.insert("", "end", values=(member["bid_member_name"], member["bid_member_type"], member["bid_member_cert_no"], member["bid_member_certificate_name"], member["bid_member_certificate_num"]))
        table.pack(fill="both", expand=True, padx=12, pady=12)

    def write_members(self) -> None:
        if not self.controller.members:
            messagebox.showwarning("没有成员资料", "请先加载并预览成员资料。")
            return
        if not self.controller.project_id:
            messagebox.showwarning("未读取项目", "请先连接软件并读取项目。")
            return
        if not messagebox.askyesno("确认写入成员信息", "将用预览的成员表覆盖软件当前“成员信息”列表，并在保存后重新读取核验。请确认姓名、身份证、证书资料与投标文件一致。继续吗？"):
            return
        try:
            self.controller.write_members_and_verify()
            self.status_var.set(f"成员信息已写入并重新读取核验成功：{len(self.controller.members)} 人。")
            messagebox.showinfo("已核验", "软件已保存，重新读取的成员信息与资料表一致。")
        except Exception as exc:
            self.status_var.set("成员信息操作停止：" + str(exc))
            messagebox.showerror("未确认成功", str(exc))

    def read_chapters(self) -> None:
        if not messagebox.askyesno("确认", "将向编制工具导入所选 .zjzbs，并只读取真实章节树；不会上传附件。继续吗？"):
            return
        try:
            bidder = PROJECT_SELF if self.bidder_var.get() == "当前项目（单单位结构）" else self.bidder_var.get()
            self.controller.set_input(self.project_var.get(), bidder, self.zjzbs_var.get())
            self.controller.connect(self.endpoint_var.get())
            self.controller.import_and_read_chapters()
            self.refresh_tree()
            self.status_var.set(f"已读取 {len(self.controller.chapters)} 个真实章节；已列出允许上传的章节。请检查匹配后，只做一条单文件验证。")
        except Exception as exc:
            self.status_var.set("停止：" + str(exc))
            messagebox.showerror("未开始上传", str(exc))

    def refresh_tree(self) -> None:
        for node in self.tree.get_children():
            self.tree.delete(node)
        for item in self.controller.items:
            file_name = Path(item.source_path).name if item.source_path else ""
            self.tree.insert("", "end", iid=item.chapter_code, values=(item.chapter_title, file_name, item.confidence, item.status, item.reason))
        self.batch_button.configure(state="normal" if self.controller.pilot_verified else "disabled")

    def selected_code(self) -> str | None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("请选择章节", "请先在列表中选择一个章节。")
            return None
        return selected[0]

    def pick_manual_source(self) -> None:
        code = self.selected_code()
        if not code or not self.controller.project_dir:
            return
        initial = bid_root(self.controller.project_dir, self.controller.bidder)
        selected = filedialog.askopenfilename(
            title="只允许选择该单位投标文件内的 PDF/Word",
            initialdir=initial,
            filetypes=[("可上传文件", "*.pdf *.doc *.docx")],
        )
        if not selected:
            return
        try:
            self.controller.set_manual_source(code, selected)
            self.refresh_tree()
        except Exception as exc:
            messagebox.showerror("不能指定此文件", str(exc))

    def verify_one(self) -> None:
        code = self.selected_code()
        if not code:
            return
        item = next(x for x in self.controller.items if x.chapter_code == code)
        if not item.source_path:
            messagebox.showwarning("缺少文件", "请先为该章节指定一个文件。")
            return
        if not messagebox.askyesno("单文件验证", f"将把\n{Path(item.source_path).name}\n上传到软件章节\n{item.chapter_title}\n并重新读取章节核验。继续吗？"):
            return
        self.controller.verify_one(code)
        self.refresh_tree()
        self.status_var.set("单文件验证完成：" + next(x for x in self.controller.items if x.chapter_code == code).reason)

    def run_batch(self) -> None:
        if not messagebox.askyesno("批量上传", "单文件核验已通过。将逐条上传、逐条重新读取章节核验；失败项会保留在报告中。继续吗？"):
            return
        self.batch_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("批量上传进行中；可随时点击“停止”。")
        threading.Thread(target=self._batch_worker, daemon=True).start()

    def _batch_worker(self) -> None:
        try:
            self.controller.run_batch()
            report = self.controller.write_report("batch")
            message = f"批量任务结束。报告：{report}"
        except Exception as exc:
            message = "批量任务异常停止：" + str(exc)
        self.root.after(0, lambda: self._finish_batch(message))

    def _finish_batch(self, message: str) -> None:
        self.stop_button.configure(state="disabled")
        self.refresh_tree()
        self.status_var.set(message)

    def stop_batch(self) -> None:
        self.controller.request_stop()
        self.stop_button.configure(state="disabled")
        self.status_var.set("已请求停止：当前软件调用返回后，不会开始下一条。")


def main() -> None:
    root = tk.Tk()
    root.title("投标章节上传助手")
    root.geometry("1250x700")
    App(root)
    root.mainloop()
