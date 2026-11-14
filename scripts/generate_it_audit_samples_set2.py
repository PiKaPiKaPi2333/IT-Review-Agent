"""生成第二批 IT 审计示例材料（10 个：Word 3 / Excel 3 / PDF 2 / JPG 2）。

复用 generate_it_audit_samples 中的字体与表格工具，输出到 samples/it_audit_set2/。
用法：python scripts/generate_it_audit_samples_set2.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import generate_it_audit_samples as g  # noqa: E402

OUT = g.ROOT / "samples" / "it_audit_set2"
random.seed(20261201)


# ───────────────────────── Word ─────────────────────────
def make_datacenter_checklist() -> None:
    doc = g.new_doc("数据中心机房环境检查表")
    g.add_table(
        doc,
        ["检查项", "检查标准", "检查结果", "备注"],
        [
            ["温湿度监控", "温度 18-27℃，湿度 40%-60%", "符合", "全年告警 2 次，已处置"],
            ["UPS 供电", "后备时间不少于 30 分钟", "符合", "2026-08 年检通过"],
            ["气体灭火系统", "每半年检测一次", "不符合", "上次检测为 2026-01，已超期"],
            ["门禁与视频", "进出记录保存 180 天", "符合", "视频保存 90 天，低于制度要求"],
            ["机柜标识", "设备双向标签完整", "部分符合", "B 区 14 个机柜缺少标签"],
            ["消防器材", "灭火器压力正常，在有效期内", "符合", ""],
        ],
    )
    doc.add_heading("检查结论", level=1)
    doc.add_paragraph(
        "机房基础设施总体运行正常。气体灭火系统检测超期、视频保存期限不足、"
        "B 区机柜标签缺失三项需整改，整改责任人为运维主管，计划于 2026 年 12 月底前完成。"
    )
    doc.save(OUT / "01_数据中心机房环境检查表.docx")


def make_outsourcing_review() -> None:
    doc = g.new_doc("外包开发商管理审计工作底稿")
    doc.add_heading("一、被审计事项", level=1)
    doc.add_paragraph(
        "信息中心与外部开发商签订的《财务报销系统二期开发服务合同》（合同编号 HX-DEV-2026-007，"
        "合同金额 186 万元）。审计关注合同履行、代码交付与权限管理。"
    )
    doc.add_heading("二、测试过程", level=1)
    g.add_table(
        doc,
        ["测试项", "测试过程", "结果"],
        [
            ["合同审批", "核对合同签署流程与采购审批单", "通过"],
            ["开发人员权限", "检查开发商人员在生产库的账号权限", "未通过：3 名开发人员持有生产库查询权限，无审批记录"],
            ["代码交付", "核对交付清单与代码仓库版本号", "部分通过：第 3 批交付缺少源代码托管记录"],
            ["验收记录", "检查验收报告与用户签字", "未通过：第 2 期验收报告由信息中心单方签署"],
            ["保密条款", "检查合同保密与数据销毁条款", "通过"],
        ],
    )
    doc.add_heading("三、审计结论", level=1)
    doc.add_paragraph(
        "外包开发过程中存在生产库权限越权与验收职责不相容两项问题，建议收回生产库权限，"
        "并由业务部门参与验收签署。"
    )
    doc.save(OUT / "02_外包开发商管理审计底稿.docx")


def make_incident_policy() -> None:
    doc = g.new_doc("信息安全事件管理办法（节选）")
    doc.add_paragraph("文件编号：IT-POL-2025-004　　版本：V1.2　　生效日期：2025-09-01")
    doc.add_heading("第一章 事件分类", level=1)
    for text in [
        "第五条 信息安全事件分为恶意代码、非授权访问、数据泄露、拒绝服务四类。",
        "第六条 发生数据泄露事件时，事件负责人应在 2 小时内向信息安全负责人报告。",
    ]:
        doc.add_paragraph(text)
    doc.add_heading("第二章 报告与处置", level=1)
    for text in [
        "第九条 一般事件由运维班组处置，处置记录须在事件关闭后 3 个工作日内归档。",
        "第十条 涉及个人信息或财务数据的事件，须由法务部门在 24 小时内完成影响评估。",
        "第十一条 事件关闭前须完成根因分析，并更新应急预案中的对应条目。",
    ]:
        doc.add_paragraph(text)
    doc.add_heading("第三章 考核", level=1)
    doc.add_paragraph("第十五条 未按规定时限报告的，对事件负责人通报批评，并纳入年度绩效考核。")
    doc.save(OUT / "03_信息安全事件管理办法_节选.docx")


# ───────────────────────── Excel ─────────────────────────
def make_jobs_log() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "批处理作业"
    jobs = ["日终结转", "应收对账", "应付付款批次", "工资计提", "固定资产折旧", "银企直连对账"]
    rows = []
    for i in range(1, 36):
        job = random.choice(jobs)
        run = date(2026, 9, 1) + timedelta(days=i)
        planned = "02:00"
        actual = f"{random.choice(['02', '02', '03']):s}:{random.randint(0, 59):02d}"
        status = "成功"
        remark = ""
        if i in (7, 19, 28):
            status, remark = "失败", "数据库连接超时，人工重跑"
        rows.append([f"JOB-{i:03d}", job, run.isoformat(), planned, actual, status, remark])
    write_sheet = g.write_sheet
    write_sheet(ws, ["作业编号", "作业名称", "运行日期", "计划时间", "实际开始", "执行结果", "备注"],
                rows, [12, 16, 12, 10, 10, 10, 28])
    wb.save(OUT / "04_批处理作业运行日志.xlsx")


def make_app_release() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "上线记录"
    rows = [
        ["REL-0901", "2026-09-05", "财务报表模块", "V3.4.1", "王*", "李*", "是", "是", "否", "正常"],
        ["REL-0912", "2026-09-12", "采购订单接口", "V2.8.0", "赵*", "赵*", "是", "否", "是", "申请人自行审批"],
        ["REL-0920", "2026-09-20", "员工报销流程", "V1.9.2", "陈*", "周*", "是", "是", "否", "正常"],
        ["REL-1003", "2026-10-03", "库存盘点小程序", "V1.0.0", "周*", "王*", "否", "否", "否", "无测试记录直接上线"],
        ["REL-1015", "2026-10-15", "税务申报接口", "V2.1.4", "李*", "陈*", "是", "是", "否", "正常"],
        ["REL-1028", "2026-10-28", "预算管理模块", "V4.0.0", "王*", "李*", "是", "否", "否", "回滚一次，原因待补充"],
    ]
    g.write_sheet(
        ws,
        ["上线单号", "上线日期", "系统模块", "版本", "开发人", "上线执行人", "测试通过", "用户验收", "有回滚方案", "备注"],
        rows,
        [12, 12, 18, 10, 10, 12, 10, 10, 12, 26],
    )
    wb.save(OUT / "05_应用程序上线记录.xlsx")


def make_reconciliation() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "接口对账差异"
    rows = []
    for i in range(1, 26):
        amt = round(random.uniform(1000, 90000), 2)
        diff = 0.0
        note = ""
        if i in (4, 13, 21):
            diff = round(random.uniform(120, 980), 2)
            note = "财务系统已记账，银行侧未达"
        rows.append([f"REC-{i:03d}", (date(2026, 10, 1) + timedelta(days=i)).isoformat(),
                     "银企直连", amt, round(amt + diff, 2), round(diff, 2),
                     "未处理" if diff else "一致", note])
    g.write_sheet(
        ws,
        ["对账编号", "日期", "接口", "财务系统金额", "银行金额", "差异金额", "状态", "说明"],
        rows,
        [12, 12, 12, 14, 14, 12, 10, 30],
    )
    wb.save(OUT / "06_银企对账差异明细.xlsx")


# ───────────────────────── PDF ─────────────────────────
def make_drill_report() -> None:
    lines = [
        f"{g.COMPANY} · {g.PREPARED}",
        "演练编号：DRILL-2026-02",
        "演练场景：勒索软件感染财务服务器，需要切换至备份系统",
        "",
        "一、演练过程",
        "09:10 监控告警发现财务服务器文件被批量加密；09:25 应急小组召集完成；",
        "09:40 隔离受感染主机；10:30 确认备份可用并开始恢复；13:05 财务系统恢复业务。",
        "",
        "二、结果评估",
        "从告警到隔离耗时 30 分钟，满足制度中 1 小时的要求。业务恢复耗时约 3 小时 55 分钟，"
        "超过预案目标的 2 小时。",
        "",
        "三、主要问题",
        "1. 应急通讯录中两位业务负责人的电话已变更，未及时通知；",
        "2. 备份恢复步骤手册仍为 2024 版本，与当前服务器配置不一致；",
        "3. 演练中未验证离线备份介质的可读性。",
        "",
        "四、改进措施",
        "责任人在 2026 年 12 月前更新通讯录与恢复手册，并在下一次演练中加入离线介质验证。",
    ]
    text = g.text_pdf
    text(OUT / "07_应急演练报告.pdf", "勒索软件应急演练报告（2026 年第二次）", lines)


def make_test_workpaper() -> None:
    lines = [
        f"{g.COMPANY} · {g.PREPARED}",
        "底稿索引：IT-WP-ACC-03",
        "测试目标：验证财务系统密码策略及账号锁定配置是否符合制度第十四条",
        "",
        "一、测试步骤",
        "1. 在系统管理后台导出密码策略参数；",
        "2. 使用测试账号连续输入错误口令 6 次，观察锁定行为；",
        "3. 对比制度要求与实际配置。",
        "",
        "二、测试结果",
        "最小口令长度：实际 6 位，制度要求 8 位，不符合；",
        "口令更换周期：实际 180 天，制度要求 90 天，不符合；",
        "错误锁定阈值：实际 5 次，符合；锁定时长 15 分钟，符合。",
        "",
        "三、证据",
        "附件 A：密码策略导出截图（截图编号 SS-ACC-03-01）；附件 B：锁定测试日志。",
        "",
        "四、复核意见",
        "测试人已完成，复核人同意上述结论。建议将整改期限定为 2026 年 12 月 31 日。",
    ]
    g.text_pdf(OUT / "08_访问控制测试底稿.pdf", "财务系统密码策略测试底稿", lines)


# ───────────────────────── JPG ─────────────────────────
def make_incident_chart() -> None:
    import matplotlib.pyplot as plt2

    fp = FontProperties(fname=g.FONT_PATH)
    months = ["4月", "5月", "6月", "7月", "8月", "9月", "10月"]
    phishing = [5, 7, 4, 9, 12, 8, 6]
    malware = [2, 1, 3, 2, 4, 1, 2]
    fig, ax = plt2.subplots(figsize=(10, 5.5), dpi=120)
    x = range(len(months))
    ax.bar([i - 0.2 for i in x], phishing, width=0.4, label="钓鱼邮件上报", color="#2F5597")
    ax.bar([i + 0.2 for i in x], malware, width=0.4, label="恶意代码告警", color="#C55A11")
    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontproperties=fp)
    ax.set_title("2026 年 4-10 月信息安全事件趋势", fontproperties=fp, fontsize=14)
    ax.set_ylabel("事件数（件）", fontproperties=fp)
    for label in ax.get_yticklabels():
        label.set_fontproperties(fp)
    ax.legend(prop=fp)
    ax.grid(axis="y", alpha=0.3)
    fig.savefig(OUT / "09_信息安全事件趋势图.jpg", format="jpg", bbox_inches="tight", facecolor="white")
    plt2.close(fig)


def make_password_screenshot() -> None:
    font = ImageFont.truetype(g.FONT_PATH, 20)
    small = ImageFont.truetype(g.FONT_PATH, 16)
    img = Image.new("RGB", (1000, 620), "#F5F5F5")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 1000, 56], fill="#404040")
    draw.text((20, 15), "统一身份认证 · 安全策略配置", font=font, fill="white")
    items = [
        ("最小口令长度", "6 位"),
        ("口令复杂度", "字母 + 数字"),
        ("口令有效期", "180 天"),
        ("历史口令禁止重复", "最近 3 次"),
        ("连续错误锁定阈值", "5 次"),
        ("锁定时长", "15 分钟"),
        ("会话空闲超时", "未设置"),
    ]
    y = 100
    for name, value in items:
        draw.text((60, y), name, font=small, fill="#222")
        draw.text((450, y), value, font=small, fill="#C00000" if value in ("6 位", "180 天", "未设置") else "#222")
        y += 62
    draw.text((60, 560), "红色项与制度 IT-POL-2025-001 第十四条不一致。", font=small, fill="#C00000")
    img.save(OUT / "10_统一身份认证安全策略截图.jpg", "JPEG", quality=92)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_datacenter_checklist()
    make_outsourcing_review()
    make_incident_policy()
    make_jobs_log()
    make_app_release()
    make_reconciliation()
    make_drill_report()
    make_test_workpaper()
    make_incident_chart()
    make_password_screenshot()
    print(f"已生成到 {OUT}")


if __name__ == "__main__":
    main()
