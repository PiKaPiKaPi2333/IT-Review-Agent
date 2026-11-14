"""生成 IT 审计示例材料（仅用于测试文档入库与问答）。

用法：python scripts/generate_it_audit_samples.py
输出：samples/it_audit/（Word 3 / Excel 3 / PDF 2 / JPG 2，共 10 个文件）
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Pt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "it_audit"
FONT_PATH = "C:/Windows/Fonts/simhei.ttf"
COMPANY = "华信科技集团"
PERIOD = "审计期间：2026-01-01 至 2026-10-31"
PREPARED = "编制日期：2026-11-13"

random.seed(20261113)
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
CJK_FONT = "Microsoft YaHei"


# ───────────────────────── Word ─────────────────────────
def apply_cjk_font(style_name: str, doc: Document) -> None:
    """设置样式的中西文字体。

    只设置 w:ascii 不够：中文走 w:eastAsia 槽位，而默认模板的标题样式用的是
    主题字体（asciiTheme 等属性优先级更高），必须把主题属性去掉再显式指定字体。
    """
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


def new_doc(title: str) -> Document:
    doc = Document()
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "List Bullet"):
        apply_cjk_font(name, doc)
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(title, level=0)
    doc.add_paragraph(f"{COMPANY}  |  {PERIOD}  |  {PREPARED}")
    return doc


def add_table(doc: Document, header: list[str], rows: list[list]) -> None:
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for i, h in enumerate(header):
        table.rows[0].cells[i].text = h
    for row in rows:
        cells = table.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)


def make_audit_plan() -> None:
    doc = new_doc("2026 年度信息系统审计方案")
    doc.add_heading("一、审计背景", level=1)
    doc.add_paragraph(
        "根据 2026 年度内部审计计划，内审部对集团信息系统的一般控制开展专项审计，"
        "重点关注 ERP 系统、OA 系统和核心数据库的访问控制、变更管理、备份恢复及职责分离情况。"
    )
    doc.add_heading("二、审计目标", level=1)
    for item in [
        "评价用户访问权限的授予、变更与注销是否经过授权并与人事变动一致；",
        "评价系统变更是否经过申请、审批、测试后上线；",
        "评价备份策略是否执行，恢复能力是否满足业务连续性要求；",
        "识别开发、上线、审批等关键职能之间的不相容权限。",
    ]:
        doc.add_paragraph(item, style="List Bullet")
    doc.add_heading("三、审计范围", level=1)
    doc.add_paragraph("ERP 系统（生产环境）、OA 系统、核心数据库（Oracle 19c）、防火墙及核心交换区域。")
    doc.add_heading("四、主要审计程序", level=1)
    add_table(
        doc,
        ["审计领域", "审计程序", "涉及系统", "抽样方式"],
        [
            ["访问控制", "比对 ERP 用户清单与 HR 在职名单，核对离职账号注销情况", "ERP、OA", "全量比对"],
            ["变更管理", "抽取 2026 年变更单，检查审批记录与测试证据", "ERP", "全量"],
            ["备份与恢复", "检查备份执行日志，抽取一次恢复测试并核对耗时", "核心数据库", "抽样"],
            ["职责分离", "检查开发、上线、审批权限是否由同一人持有", "ERP、OA", "全量"],
            ["网络安全", "检查防火墙规则的审批记录与边界开放端口", "网络", "抽样"],
        ],
    )
    doc.add_heading("五、时间安排", level=1)
    add_table(
        doc,
        ["阶段", "时间", "产出"],
        [
            ["现场准备", "2026-10-20 至 10-24", "审计方案、访谈提纲"],
            ["现场测试", "2026-10-27 至 11-07", "测试底稿、证据清单"],
            ["报告出具", "2026-11-09 至 11-20", "审计报告草稿、整改建议"],
        ],
    )
    doc.add_heading("六、审计组成员", level=1)
    doc.add_paragraph("审计组长 1 名，IT 审计人员 2 名，业务审计人员 1 名（财务流程）。")
    doc.save(OUT / "01_信息系统审计方案.docx")


def make_interview_notes() -> None:
    doc = new_doc("IT 总体控制访谈纪要")
    add_table(
        doc,
        ["项目", "内容"],
        [
            ["访谈日期", "2026-10-21 14:00-15:30"],
            ["访谈对象", "信息中心负责人（受访人A）、运维主管（受访人B）"],
            ["访谈人", "内审部 IT 审计组"],
            ["访谈地点", "信息中心会议室"],
        ],
    )
    doc.add_heading("一、访问权限管理", level=1)
    doc.add_paragraph("问：新员工开通 ERP 账号的流程是什么？")
    doc.add_paragraph("答（受访人B）：由部门负责人在 OA 提交开通申请，信息中心按角色模板授权，授权单留存在 OA 中。")
    doc.add_paragraph("问：离职人员账号如何注销？")
    doc.add_paragraph("答（受访人B）：HR 在离职流程中通知信息中心，理论上当日注销，实际依赖人工通知，缺少与 HR 系统的自动对接。")
    doc.add_heading("二、变更管理", level=1)
    doc.add_paragraph("问：紧急变更是否有例外流程？")
    doc.add_paragraph("答（受访人A）：有。紧急变更可先上线、事后 3 个工作日内补办审批，但未明确补办的监督机制。")
    doc.add_heading("三、备份与恢复", level=1)
    doc.add_paragraph("答（受访人B）：核心数据库每日增量备份、每周全量备份，备份保留 180 天，每季度做一次恢复演练。")
    doc.add_heading("四、待跟进事项", level=1)
    for item in [
        "索取 2026 年度离职人员清单及对应账号注销记录；",
        "索取紧急变更补办审批的台账；",
        "索取 2026 年 Q1-Q3 恢复演练报告。",
    ]:
        doc.add_paragraph(item, style="List Bullet")
    doc.save(OUT / "02_IT总体控制访谈纪要.docx")


def make_policy_excerpt() -> None:
    doc = new_doc("信息安全管理制度（节选）")
    doc.add_paragraph("文件编号：IT-POL-2025-001　　版本：V2.1　　生效日期：2025-07-01")
    doc.add_heading("第一章 账号与权限管理", level=1)
    for text in [
        "第十二条 用户账号须经部门负责人书面申请、信息中心审批后方可开通，并按最小授权原则分配角色。",
        "第十三条 员工离职，人力资源部应于离职当日通知信息中心，信息中心应于当日注销其全部系统账号。",
        "第十四条 口令长度不少于 8 位，须包含字母与数字，每 90 天更换一次。",
        "第十五条 超级管理员账号由信息中心负责人指定，每季度复核一次，禁止日常业务使用。",
    ]:
        doc.add_paragraph(text)
    doc.add_heading("第二章 变更管理", level=1)
    for text in [
        "第二十条 生产系统变更须提交变更申请，经业务负责人与信息中心负责人审批，并在测试环境验证通过后方可上线。",
        "第二十一条 开发人员不得同时担任本人变更的审批人或上线执行人。",
        "第二十二条 紧急变更须经信息中心负责人口头批准，并在 3 个工作日内补办书面审批。",
    ]:
        doc.add_paragraph(text)
    doc.add_heading("第三章 备份与恢复", level=1)
    for text in [
        "第三十条 核心系统每日执行增量备份，每周执行全量备份，备份数据保留不少于 180 天。",
        "第三十一条 每季度至少执行一次恢复演练，演练结果形成书面报告，恢复时间须满足 RTO 4 小时的要求。",
    ]:
        doc.add_paragraph(text)
    doc.save(OUT / "03_信息安全管理制度_节选.docx")


# ───────────────────────── Excel ─────────────────────────
def write_sheet(ws, header: list[str], rows: list[list], widths: list[int]) -> None:
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


def make_access_list() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "ERP用户权限"
    depts = ["财务部", "采购部", "销售部", "人力资源部", "生产部", "信息中心", "仓储物流部"]
    roles = ["总账会计", "应付审核", "应收专员", "采购员", "库管员", "HR专员", "生产计划员", "系统管理员"]
    rows = []
    for i in range(1, 41):
        uid = f"U{1000 + i}"
        dept = random.choice(depts)
        role = random.choice(roles)
        granted = date(2026, 1, 5) + timedelta(days=random.randint(0, 250))
        last_login = date(2026, 10, 31) - timedelta(days=random.randint(0, 20))
        rows.append([uid, f"员工{i:02d}", dept, "ERP", role, granted.isoformat(),
                     "部门负责人", f"APR-2026-{i:04d}", last_login.isoformat(), "启用", ""])

    # 人为植入的审计发现，便于测试问答
    rows[16] = ["U1017", "员工17", "采购部", "ERP", "采购员", "2025-03-10", "部门负责人",
                "APR-2025-0311", "2026-08-12", "启用", "2026-06-30 已离职，账号仍可登录"]
    rows[22] = ["U1023", "员工23", "信息中心", "ERP", "超级管理员", "2026-02-01", "",
                "", "2026-10-30", "启用", "无审批记录"]
    rows[30] = ["U1031", "员工31", "财务部", "ERP", "总账会计 + 应付审核", "2026-04-18", "部门负责人",
                "APR-2026-0412", "2026-10-29", "启用", "同一人同时具备录入与审核权限"]
    write_sheet(ws, ["用户ID", "姓名", "部门", "系统", "角色", "授权日期", "审批人",
                     "审批单号", "最后登录", "账号状态", "备注"], rows,
                [10, 10, 12, 8, 20, 12, 14, 16, 12, 10, 34])
    wb.save(OUT / "04_ERP用户访问权限清单.xlsx")


def make_change_log() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "变更台账"
    rows = []
    systems = ["ERP", "OA", "核心数据库"]
    kinds = ["功能变更", "参数调整", "补丁升级", "报表变更"]
    people = ["王*", "李*", "赵*", "陈*", "周*"]
    for i in range(1, 31):
        req = random.choice(people)
        appr = random.choice([p for p in people if p != req])
        tester = random.choice([p for p in people if p not in (req, appr)])
        day = date(2026, 1, 8) + timedelta(days=random.randint(0, 280))
        rows.append([f"CHG-2026-{i:04d}", day.isoformat(), random.choice(systems), random.choice(kinds),
                     "常规变更", req, appr, tester, "是", "否", ""])
    rows[11][6] = rows[11][5]  # 申请人与审批人相同
    rows[11][10] = "申请人自行审批"
    rows[18][8] = "否"
    rows[18][10] = "测试证据缺失"
    rows[24][9] = "是"
    rows[24][4] = "紧急变更"
    rows[24][10] = "紧急上线，事后补办审批"
    write_sheet(ws, ["变更单号", "申请日期", "系统", "变更类型", "变更说明", "申请人", "审批人",
                     "测试人", "有测试记录", "紧急变更", "备注"], rows,
                [16, 12, 12, 12, 12, 10, 10, 10, 12, 12, 26])
    wb.save(OUT / "05_变更管理台账.xlsx")


def make_sod_check() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "职责分离检查"
    rows = [
        ["U1031", "员工31", "财务部", "总账录入", "应付审核", "是", "同时具备录入与审核，构成不相容"],
        ["U1044", "员工44", "采购部", "采购下单", "收货确认", "否", ""],
        ["U1052", "员工52", "信息中心", "系统开发", "生产上线", "是", "开发人员可直接上线生产库"],
        ["U1060", "员工60", "仓储物流部", "库存调整", "库存盘点复核", "否", ""],
        ["U1068", "员工68", "销售部", "客户信用维护", "应收核销", "是", "可自行调整信用额度并核销"],
    ]
    write_sheet(ws, ["用户ID", "姓名", "部门", "职能A", "职能B", "是否冲突", "说明"], rows,
                [10, 10, 12, 16, 16, 10, 40])
    wb.save(OUT / "06_职责分离冲突检查.xlsx")


# ───────────────────────── PDF（matplotlib） ─────────────────────────
def text_pdf(path: Path, title: str, lines: list[str], width: int = 44, per_page: int = 52) -> None:
    fp = FontProperties(fname=FONT_PATH)
    wrapped = [title, ""]
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped += [line[i : i + width] for i in range(0, len(line), width)]
    with PdfPages(path) as pdf:
        for start in range(0, len(wrapped), per_page):
            fig = plt.figure(figsize=(8.27, 11.69))
            y = 0.95
            for text in wrapped[start : start + per_page]:
                fig.text(0.08, y, text, fontproperties=fp, fontsize=10, va="top")
                y -= 0.0175
            pdf.savefig(fig)
            plt.close(fig)


def make_backup_report() -> None:
    lines = [
        f"{COMPANY} · {PREPARED}",
        "报告编号：BK-RPT-2026-Q3",
        "测试对象：核心数据库（Oracle 19c，生产库 ERP_PROD）",
        "测试方式：从 2026-09-18 的全量备份集恢复至隔离测试环境",
        "",
        "一、测试结果",
        "本次恢复耗时 3 小时 42 分钟，RTO 目标为 4 小时，满足要求。恢复后抽查 30 张核心表，记录数与生产库一致。",
        "",
        "二、备份执行情况（2026 年 7 月至 9 月）",
        "增量备份执行率 98.9%（273 次中 3 次失败），全量备份执行率 100%（13 次）。",
        "失败的 3 次增量备份均发生在 2026-08-03、2026-08-17、2026-09-07，原因为存储空间不足，已于次日补做。",
        "",
        "三、发现问题",
        "1. 备份介质未加密，异地副本仅保存 30 天，低于制度要求的 180 天。",
        "2. 上一次恢复演练距今已超过 90 天，不符合季度演练的要求（上次为 2026-06-20）。",
        "",
        "四、整改建议",
        "建议在 2026 年 12 月前完成备份加密、延长异地副本保留期，并按季度安排恢复演练。",
    ]
    text_pdf(OUT / "07_备份恢复测试报告.pdf", "备份恢复测试报告（2026 年第三季度）", lines)


def make_emergency_plan() -> None:
    lines = [
        f"{COMPANY} · {PREPARED}",
        "文件编号：IT-EMG-2025-003　版本：V1.4",
        "",
        "一、适用范围",
        "本预案适用于 ERP、OA、核心数据库及网络基础设施发生的重大故障或安全事件。",
        "",
        "二、事件分级",
        "一级（特别重大）：核心系统停机超过 4 小时，或数据丢失超过 1 小时业务量。",
        "二级（重大）：核心系统停机 1 至 4 小时，或发生确认的数据泄露事件。",
        "三级（一般）：非核心系统故障，或单点故障已被冗余机制吸收。",
        "",
        "三、响应流程",
        "1. 值班人员在 15 分钟内确认事件并上报信息中心负责人；",
        "2. 一级、二级事件 30 分钟内启动应急小组，通知业务负责人与财务负责人；",
        "3. 根据备份恢复方案执行恢复，恢复过程形成操作记录；",
        "4. 事件关闭后 5 个工作日内提交复盘报告。",
        "",
        "四、通讯录与升级机制",
        "应急小组联系方式每季度更新一次。若最后一次更新超过 6 个月，视为通讯录失效。",
        "",
        "五、演练要求",
        "每年至少组织一次综合演练。2026 年演练计划为 11 月进行，目前尚未执行。",
    ]
    text_pdf(OUT / "08_信息系统应急预案_摘要.pdf", "信息系统应急预案（摘要）", lines)


# ───────────────────────── JPG ─────────────────────────
def make_network_diagram() -> None:
    fp = FontProperties(fname=FONT_PATH)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    def box(x, y, w, h, label, color):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05", fc=color, ec="#333"))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontproperties=fp, fontsize=10)

    box(0.2, 2.5, 1.6, 1.0, "互联网", "#EEEEEE")
    box(2.3, 2.5, 1.6, 1.0, "防火墙\n（边界）", "#F8CBAD")
    box(4.4, 4.2, 1.8, 1.0, "DMZ：Web 服务器", "#DDEBF7")
    box(4.4, 2.5, 1.8, 1.0, "应用服务器区\nERP / OA", "#DDEBF7")
    box(4.4, 0.8, 1.8, 1.0, "数据库区\n核心数据库", "#DDEBF7")
    box(7.0, 2.5, 1.8, 1.0, "办公网\n约 320 终端", "#E2EFDA")
    box(7.0, 0.8, 1.8, 1.0, "备份中心\n（同城）", "#E2EFDA")

    arrows = [((1.8, 3.0), (2.3, 3.0)), ((3.9, 3.0), (4.4, 4.7)), ((3.9, 3.0), (4.4, 3.0)),
              ((6.2, 3.0), (7.0, 3.0)), ((6.2, 1.3), (7.0, 1.3)), ((5.3, 2.5), (5.3, 1.8))]
    for start, end in arrows:
        ax.annotate("", xy=end, xytext=start, arrowprops=dict(arrowstyle="->", color="#333"))
    ax.text(0.2, 5.6, "图 1　华信科技集团信息系统网络拓扑",
            fontproperties=fp, fontsize=11)
    ax.text(0.2, 0.2, "注：防火墙规则 FW-2026-031 为待整改项（管理端口对内网全部开放）",
            fontproperties=fp, fontsize=8, color="#A00000")
    fig.savefig(OUT / "09_系统网络拓扑图.jpg", format="jpg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_door_log_screenshot() -> None:
    font = ImageFont.truetype(FONT_PATH, 20)
    small = ImageFont.truetype(FONT_PATH, 16)
    img = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 1200, 60], fill="#2F5597")
    draw.text((20, 16), "机房门禁系统 · 进出记录导出（2026-10-12）", font=font, fill="white")
    headers = ["时间", "门禁点", "卡号/人员", "方向", "结果"]
    xs = [30, 230, 470, 760, 920]
    y = 90
    for x, h in zip(xs, headers):
        draw.text((x, y), h, font=font, fill="#1F1F1F")
    draw.line([0, y + 36, 1200, y + 36], fill="#999", width=2)
    records = [
        ("2026-10-12 08:42:10", "机房A-主门", "员工09 (IT-0912)", "进", "成功"),
        ("2026-10-12 12:05:33", "机房A-主门", "员工09 (IT-0912)", "出", "成功"),
        ("2026-10-12 19:42:18", "机房A-主门", "IT-0923（运维）", "进", "成功"),
        ("2026-10-12 19:43:02", "机房B-备份间", "IT-0923（运维）", "进", "成功"),
        ("2026-10-12 21:10:55", "机房A-主门", "（无卡号）", "进", "失败"),
        ("2026-10-12 21:11:20", "机房A-主门", "（无卡号）", "进", "开门（人工放行）"),
        ("2026-10-12 23:58:40", "机房A-主门", "IT-0923（运维）", "出", "成功"),
    ]
    y += 52
    for rec in records:
        for x, val in zip(xs, rec):
            draw.text((x, y), val, font=small, fill="#1F1F1F")
        y += 44
    draw.text((30, 630), "注：标注“人工放行”的记录未在系统中登记审批单。",
              font=small, fill="#A00000")
    img.save(OUT / "10_机房门禁进出记录截图.jpg", "JPEG", quality=92)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_audit_plan()
    make_interview_notes()
    make_policy_excerpt()
    make_access_list()
    make_change_log()
    make_sod_check()
    make_backup_report()
    make_emergency_plan()
    make_network_diagram()
    make_door_log_screenshot()
    print(f"已生成到 {OUT}")


if __name__ == "__main__":
    main()
