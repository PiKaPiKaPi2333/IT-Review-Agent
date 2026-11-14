"""生成「离职账号注销」相关的虚拟样本材料（仅用于测试文档入库与问答，非真实制度）。

用法：python -m scripts.generate_offboarding_samples
输出：samples/it_audit/离职注销_*.docx、离职注销_*.xlsx

包含：
  1. 离职注销_账号管理办法_节选.docx     —— 注销流程、时限、留痕要求
  2. 离职注销_复核与监督办法.docx         —— 复核、审批与监督机制
  3. 离职注销_对接控制要求.docx           —— HR 系统与信息中心的自动化控制
  4. 离职注销_台账与操作日志.xlsx         —— 注销台账 + 系统操作日志（执行证据，含若干例外）

所有样本的编制单位、制度编号均为虚构，文件内会标注"虚拟样本"。
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "it_audit"

COMPANY = "华信科技集团"
PERIOD = "审计期间：2026-01-01 至 2026-10-31"
PREPARED = "编制日期：2026-11-13"
DISCLAIMER = "【虚拟样本：仅用于测试，不代表真实制度或业务记录】"
CJK_FONT = "Microsoft YaHei"
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")

random.seed(20261114)


# ───────────────────────── Word 公共函数 ─────────────────────────
def _apply_cjk_font(style_name: str, doc: Document) -> None:
    style = doc.styles[style_name]
    style.font.name = CJK_FONT
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attr), None)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), CJK_FONT)


def _new_doc(title: str) -> Document:
    doc = Document()
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "List Bullet"):
        _apply_cjk_font(name, doc)
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(title, level=0)
    doc.add_paragraph(f"{COMPANY}  |  {PERIOD}  |  {PREPARED}")
    doc.add_paragraph(DISCLAIMER)
    return doc


def _add_table(doc: Document, header: list[str], rows: list[list]) -> None:
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for i, h in enumerate(header):
        table.rows[0].cells[i].text = h
    for row in rows:
        cells = table.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)


def _clauses(doc: Document, items: list[str]) -> None:
    for text in items:
        doc.add_paragraph(text)


# ───────────────────────── 1. 账号管理办法（节选） ─────────────────────────
def make_account_policy() -> None:
    doc = _new_doc("账号管理办法（节选）")
    doc.add_paragraph("文件编号：IT-POL-2025-007　　版本：V1.3　　生效日期：2025-09-01")

    doc.add_heading("第一章 离职账号注销流程与时限", level=1)
    _clauses(doc, [
        "第一条 员工办理离职手续时，人力资源部应在离职生效日当日，通过《离职注销工单》通知信息中心，工单须注明工号、姓名、部门、离职生效日期及需注销的系统清单。",
        "第二条 信息中心收到工单后，应于离职生效日当日完成以下操作：注销员工在 ERP、OA、核心数据库、邮件系统及 VPN 中的全部账号；收回已分配的角色与数据权限。",
        "第三条 对于在离职生效日之前已提出申请的离职，人力资源部可提前通知信息中心，信息中心应在离职生效日当日执行注销，不得提前注销导致员工无法完成工作交接。",
        "第四条 因系统故障或工单延误无法当日完成注销的，信息中心应于次一工作日上午 10 时前完成，并在工单备注中说明原因，同时通知信息中心主管。",
        "第五条 离职员工为外包人员、借调人员或实习人员的，其账号注销时限与正式员工相同，由用工部门负责人在工单中确认离职日期。",
    ])

    doc.add_heading("第二章 注销时限汇总", level=1)
    _add_table(doc, ["事项", "责任部门", "时限要求"], [
        ["离职工单发起", "人力资源部", "离职生效日当日"],
        ["核心系统账号注销（ERP、核心数据库）", "信息中心", "离职生效日当日"],
        ["其他系统账号注销（OA、邮件、VPN）", "信息中心", "离职生效日当日"],
        ["因故延误的补充注销", "信息中心", "次一工作日 10:00 前"],
    ])

    doc.add_heading("第三章 操作留痕要求", level=1)
    _clauses(doc, [
        "第六条 每一次账号注销必须对应一张《离职注销工单》，工单编号格式为 LZ-年份-序号，工单须包含工单号、员工工号、注销系统、注销时间、执行人和复核人。",
        "第七条 信息中心须在各系统中保留账号注销的操作日志，日志应至少包含操作时间、操作账号、目标账号、操作类型和执行结果，保存期限不少于 3 年。",
        "第八条 信息中心须维护《离职账号注销台账》，按月汇总当月工单，台账字段与工单一致，并与操作日志相互核对。",
        "第九条 工单、操作日志与台账构成注销执行证据。审计时须能够通过工单号在三者之间互相追溯。",
        "第十条 注销完成后，若同一账号在离职生效日之后仍出现登录或操作记录，视为注销未生效，信息中心须在一个工作日内查明原因并报告。",
    ])

    doc.add_heading("第四章 附则", level=1)
    _clauses(doc, [
        "第十一条 本办法由信息中心负责解释，与《信息安全管理制度》不一致之处，以本办法为准。",
        "第十二条 本办法自生效之日起施行。",
    ])
    doc.save(OUT / "离职注销_账号管理办法_节选.docx")


# ───────────────────────── 2. 复核与监督办法 ─────────────────────────
def make_review_policy() -> None:
    doc = _new_doc("离职账号注销复核与监督办法")
    doc.add_paragraph("文件编号：IT-POL-2025-008　　版本：V1.0　　生效日期：2025-09-01")

    doc.add_heading("第一章 复核与审批", level=1)
    _clauses(doc, [
        "第一条 离职账号注销工单须由信息中心复核人复核，复核人应为信息中心主管或其指定人员，且不得为本工单的执行人。",
        "第二条 复核人应于注销完成后一个工作日内，核对工单内容与各系统注销记录，确认无误后在工单上签字确认；未复核的工单视为未完成。",
        "第三条 注销执行人与复核人不得为同一人。执行人与复核人同时存在于同一工单时，须由信息中心主管另行审批。",
        "第四条 对于超级管理员账号或具备审批权限的账号，注销前须经信息中心负责人书面批准，批准记录与工单一并保存。",
    ])

    doc.add_heading("第二章 定期监督", level=1)
    _clauses(doc, [
        "第五条 信息中心每月末抽取当月全部离职注销工单，核对工单、操作日志与台账的一致性，形成月度核对记录。",
        "第六条 内审部每季度将 HR 在职名单与各系统账号清单进行全量比对，比对结果作为审计证据留存。",
        "第七条 比对发现离职人员仍有有效账号的，信息中心须在三个工作日内完成注销，并说明原因；无正当理由超过三个工作日的，计入内部控制缺陷。",
        "第八条 信息中心主管对月度核对记录进行审阅签字，未签字的核对记录视为无效。",
    ])

    doc.add_heading("第三章 责任与考核", level=1)
    _clauses(doc, [
        "第九条 因注销不及时导致离职人员仍可登录系统的，由信息中心负责人承担管理责任，并在绩效考核中体现。",
        "第十条 HR 未按时发起离职工单导致注销延误的，由人力资源部负责人说明原因，并纳入月度通报。",
    ])
    doc.save(OUT / "离职注销_复核与监督办法.docx")


# ───────────────────────── 3. HR 与信息中心对接控制要求 ─────────────────────────
def make_hr_integration_policy() -> None:
    doc = _new_doc("HR 系统与信息中心离职对接控制要求")
    doc.add_paragraph("文件编号：IT-CTL-2025-012　　版本：V1.1　　生效日期：2025-10-01")

    doc.add_heading("第一章 对接方式", level=1)
    _clauses(doc, [
        "第一条 人力资源系统（HRMS）每个工作日 07:30 向信息中心的账号管理系统推送离职名单，推送内容包括工号、姓名、部门、离职生效日期和需注销的系统清单。",
        "第二条 离职名单推送采用接口方式，推送记录须包含推送时间、记录数量和接口返回状态，并保存 3 年。",
        "第三条 接口不可用时，人力资源部应于当日以《离职注销工单》形式手工发送名单，并在次一工作日补录至接口。",
    ])

    doc.add_heading("第二章 自动化控制要求", level=1)
    _clauses(doc, [
        "第四条 账号管理系统收到离职名单后，须自动生成注销工单，并按离职生效日期排定注销任务，离职生效日当日未完成的任务自动升级提醒信息中心主管。",
        "第五条 账号管理系统须对工号进行校验，工号在 HR 在职名单中不存在或已离职的，不得生成注销工单，并应记录异常。",
        "第六条 接口推送失败须自动告警，告警信息发送至信息中心运维值班人员与人力资源部对接人，并在两小时内完成人工处理。",
        "第七条 每日终了，信息中心须将账号管理系统的注销工单数量与 HRMS 推送的离职人数进行对账，差异须在次一工作日前说明。",
        "第八条 HRMS 中的离职生效日期发生变更的，须同步更新账号管理系统中的注销计划，变更记录须保留。",
    ])

    doc.add_heading("第三章 权限与变更管理", level=1)
    _clauses(doc, [
        "第九条 对接接口的服务账号仅具备推送离职名单的权限，不得用于登录业务系统。",
        "第十条 对接接口的变更须经信息中心负责人审批，并在测试环境验证通过后上线。",
        "第十一条 每季度信息中心须对服务账号的权限进行复核，复核记录作为审计证据保存。",
    ])
    doc.save(OUT / "离职注销_对接控制要求.docx")


# ───────────────────────── 4. 台账与操作日志（Excel） ─────────────────────────
def _write_sheet(ws, header: list[str], rows: list[list], widths: list[int]) -> None:
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        ws.append(row)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    ws.freeze_panes = "A2"


DEPTS = ["财务部", "采购部", "人力资源部", "信息中心", "营销部", "审计部", "运营部"]
SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨")
GIVEN = list("伟芳娜秀英敏静丽强磊洋艳勇军杰娟涛明超")
SYSTEMS = ["ERP", "OA", "核心数据库", "邮件系统", "VPN"]
EXECUTORS = ["王志远", "李思涵", "张雨桐"]
REVIEWERS = ["陈建国", "刘晓燕"]


def _make_ledger_rows() -> tuple[list[list], list[dict]]:
    """生成 40 张工单。大部分正常，其中少数为刻意设置的例外，便于测试。"""
    rows: list[list] = []
    meta: list[dict] = []
    start = date(2026, 1, 5)
    for i in range(1, 41):
        leave_day = start + timedelta(days=random.randint(0, 280))
        while leave_day.weekday() >= 5:
            leave_day += timedelta(days=1)
        emp_no = f"E{20000 + i * 7}"
        name = random.choice(SURNAMES) + random.choice(GIVEN) + random.choice(GIVEN)
        dept = random.choice(DEPTS)
        order_no = f"LZ-2026-{i:04d}"

        notify = leave_day - timedelta(days=random.choice([0, 0, 0, 1]))
        executed = leave_day
        reviewer = random.choice(REVIEWERS)
        executor = random.choice(EXECUTORS)
        remark = ""
        status = "已完成"
        exception = None

        if i in (7, 19, 28):  # 注销延迟两天
            executed = leave_day + timedelta(days=2)
            exception = "注销延迟"
            remark = "系统维护，延后注销"
        elif i in (12, 33):  # 复核缺失
            reviewer = ""
            exception = "复核缺失"
            status = "待复核"
        elif i in (23,):  # 通知晚于离职日
            notify = leave_day + timedelta(days=1)
            exception = "HR 通知滞后"
            remark = "HR 工单次日补发"

        if exception == "HR 通知滞后" and notify > executed:
            executed = notify

        systems = random.sample(SYSTEMS, k=random.choice([3, 4, 5]))
        rows.append([
            order_no, emp_no, name, dept,
            leave_day.isoformat(), notify.isoformat(),
            "、".join(systems), executed.isoformat(),
            executor, reviewer, status, remark,
        ])
        meta.append({
            "order_no": order_no, "emp_no": emp_no, "leave": leave_day,
            "executed": executed, "systems": systems, "exception": exception,
        })
    return rows, meta


def _make_ops_log(meta: list[dict]) -> list[list]:
    """操作日志：每个工单对应若干注销记录；为两名例外人员保留离职后的登录记录。"""
    logs: list[list] = []
    late_login_orders = {"LZ-2026-0007": "ERP", "LZ-2026-0028": "OA"}
    for m in meta:
        base = datetime.combine(m["executed"], datetime.min.time()) + timedelta(hours=9, minutes=random.randint(0, 120))
        for k, sys_name in enumerate(m["systems"]):
            t = base + timedelta(minutes=k * 5)
            logs.append([
                t.strftime("%Y-%m-%d %H:%M:%S"), sys_name, "svc_account_admin",
                "禁用账号", m["emp_no"], "成功", m["order_no"],
            ])
        if m["order_no"] in late_login_orders:
            sys_name = late_login_orders[m["order_no"]]
            # 注销完成次日仍有登录成功记录：注销未真正生效
            t = datetime.combine(m["executed"] + timedelta(days=1), datetime.min.time()) + timedelta(hours=20, minutes=12)
            logs.append([
                t.strftime("%Y-%m-%d %H:%M:%S"), sys_name, m["emp_no"],
                "登录", m["emp_no"], "成功", "（无关联工单）",
            ])
    logs.sort(key=lambda r: r[0])
    return logs


def make_ledger_workbook() -> None:
    wb = Workbook()

    ws = wb.active
    ws.title = "注销台账"
    ledger_rows, meta = _make_ledger_rows()
    _write_sheet(
        ws,
        ["工单号", "工号", "姓名", "部门", "离职生效日", "HR 通知日", "注销系统",
         "注销完成日", "执行人", "复核人", "工单状态", "备注"],
        ledger_rows,
        [16, 10, 10, 12, 13, 13, 30, 13, 10, 10, 10, 22],
    )

    ws2 = wb.create_sheet("系统操作日志")
    _write_sheet(
        ws2,
        ["操作时间", "系统", "操作账号", "操作类型", "目标账号", "结果", "关联工单"],
        _make_ops_log(meta),
        [20, 12, 16, 12, 12, 10, 26],
    )

    ws3 = wb.create_sheet("说明")
    notes = [
        ["项目", "内容"],
        ["文件性质", "虚拟样本，仅用于测试文档入库与问答，非真实业务记录"],
        ["台账范围", "2026 年 1 月至 10 月离职人员（样本 40 张工单）"],
        ["例外标记", "工单 LZ-2026-0007/0019/0028 注销延迟两天；LZ-2026-0012/0033 缺少复核人；LZ-2026-0023 HR 通知滞后"],
        ["日志例外", "工单 LZ-2026-0007（ERP）与 LZ-2026-0028（OA）对应账号在注销完成次日仍有登录成功记录"],
    ]
    for row in notes:
        ws3.append(row)
    for cell in ws3[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
    ws3.column_dimensions["A"].width = 14
    ws3.column_dimensions["B"].width = 90

    wb.save(OUT / "离职注销_台账与操作日志.xlsx")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_account_policy()
    make_review_policy()
    make_hr_integration_policy()
    make_ledger_workbook()
    print(f"已生成到 {OUT}")


if __name__ == "__main__":
    main()
