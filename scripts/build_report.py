#!/usr/bin/env python3
"""Build phone-readable Markdown and DOCX reports from aggregate experiment outputs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
REPORTS = ROOT / "reports"

MODEL_LABELS = {
    "prevalence_baseline": "Baseline tỷ lệ nền",
    "logistic_regression": "Logistic Regression",
    "shallow_decision_tree": "Decision Tree nông",
}


def pct(value: float) -> str:
    return f"{value:.1%}"


def dec(value: float) -> str:
    return f"{value:.3f}"


def make_report_markdown(summary: dict, metrics: pd.DataFrame, terms: pd.DataFrame, rules: str, fairness: pd.DataFrame) -> str:
    cohort = summary["cohort"]
    splits = summary["splits"]
    rows = []
    for _, row in metrics.iterrows():
        rows.append(
            f"| {MODEL_LABELS.get(row['model'], row['model'])} | {dec(row['pr_auc_average_precision'])} | "
            f"{dec(row['roc_auc'])} | {pct(row['precision'])} | {pct(row['recall_sensitivity'])} | "
            f"{pct(row['specificity'])} | {dec(row['f1'])} | {dec(row['brier_score'])} |"
        )
    result_table = "\n".join(rows)
    term_lines = []
    for _, row in terms.head(10).iterrows():
        direction = "liên hệ với odds cao hơn" if row["coefficient_log_odds"] > 0 else "liên hệ với odds thấp hơn"
        term_lines.append(
            f"- `{row['feature']}`: hệ số {row['coefficient_log_odds']:+.3f}, OR={row['odds_ratio']:.2f}, "
            f"support train={int(row['training_support']):,} ({direction} trong mô hình)."
        )
    if not term_lines:
        term_lines = ["- Chưa có bảng hệ số; chạy lại pipeline để sinh bảng giải thích."]
    fairness_note = ""
    if len(fairness):
        fairness_note = f"Bảng tổng hợp hiện có {len(fairness)} nhóm (mỗi nhóm tối thiểu 200 encounter); xem `../outputs/tables/subgroup_metrics.csv`."
    else:
        fairness_note = "Không có nhóm nào vượt ngưỡng công bố 200 encounter trong lần chạy này; vì vậy không xuất bảng nhóm."

    logistic = metrics.loc[metrics["model"] == "logistic_regression"].iloc[0]
    tree = metrics.loc[metrics["model"] == "shallow_decision_tree"].iloc[0]
    baseline = metrics.loc[metrics["model"] == "prevalence_baseline"].iloc[0]
    best_name = "Logistic Regression" if logistic["pr_auc_average_precision"] >= tree["pr_auc_average_precision"] else "Decision Tree nông"
    best_ap = max(logistic["pr_auc_average_precision"], tree["pr_auc_average_precision"])
    markdown = f"""# Dự báo nhãn early-readmission `<30` theo UCI ở người bệnh đái tháo đường

## Kết luận chính

Trên tập test không trùng bệnh nhân với train/validation, mô hình có Average Precision cao hơn là **{best_name}** ({best_ap:.3f}), so với prevalence baseline {baseline['pr_auc_average_precision']:.3f}. Logistic Regression đạt precision {pct(logistic['precision'])} và recall {pct(logistic['recall_sensitivity'])} tại threshold được chọn trên validation; Decision Tree nông đạt lần lượt {pct(tree['precision'])} và {pct(tree['recall_sensitivity'])}. Đây là kết quả dự báo ở dữ liệu lịch sử, không phải bằng chứng cho thấy can thiệp dựa trên mô hình sẽ cải thiện sức khỏe.

## Dữ liệu và cách xác định cohort

Nguồn là [UCI Diabetes 130-US Hospitals for Years 1999–2008](https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008), ghi công [Clore et al. (2014), DOI 10.24432/C5230J](https://doi.org/10.24432/C5230J). Dữ liệu gốc có {cohort['raw_rows']:,} encounter và {cohort['raw_unique_patients']:,} bệnh nhân; {cohort['raw_patients_with_multiple_encounters']:,} người có nhiều encounter. Mỗi hàng phân tích là một encounter.

Outcome được giữ đúng nhãn UCI: `<30` = 1, còn `>30` hoặc `NO` = 0. Cohort chính có **{cohort['eligible_encounters']:,} encounter**, trong đó {cohort['positive_encounters']:,} nhãn `<30` ({pct(cohort['positive_rate'])}). Loại {cohort['excluded_death_dispositions']:,} disposition tử vong, {cohort['excluded_hospice_dispositions']:,} hospice và cả {cohort['excluded_pending_discharge_disposition_12']:,} encounter mã 12 (“still patient/expected outpatient return”): không encounter mã 12 nào được giữ vì chưa thể hiện lần xuất viện hoàn tất. Không chạy sensitivity riêng bằng cách thêm lại ba encounter này. Các encounter chuyển viện vẫn được giữ; transfer có thể là tiếp nối cùng đợt điều trị nên phải diễn giải thận trọng.

Bài báo gốc xác định nhãn dựa trên lần inpatient đầu tiên tiếp theo được ghi nhận sau xuất viện. Dữ liệu từ nhà cung cấp ngoài mạng lưới không được quan sát. Nhãn `<30` vì vậy không nhất thiết là tái nhập viện không kế hoạch, không phải chỉ số risk-adjusted/CMS và không thể đồng nhất với mọi định nghĩa 30 ngày khác.

## Thiết kế mô hình

Các trường nhận diện `encounter_id` và `patient_nbr` không được dùng làm predictor; `patient_nbr` chỉ dùng để chia nhóm. Nhóm chẩn đoán ICD-9 được gom thành phạm trù rộng. `weight` bị loại do thiếu rất nhiều dữ liệu; `payer_code` bị loại vì tính hành chính/khả năng khái quát thấp. `gender` và `race` bị loại khỏi đặc trưng của tất cả mô hình chính; chúng chỉ được dùng để audit subgroup trên dự đoán của Logistic Regression không nhận các thuộc tính này làm đầu vào. `age` vẫn là predictor và cũng được dùng để chia nhóm audit. Không dùng outcome để tạo đặc trưng.

Dùng `StratifiedGroupKFold` xấp xỉ 60/20/20: train {splits['train']['encounters']:,}, validation {splits['validation']['encounters']:,}, test {splits['test']['encounters']:,}. Mỗi bệnh nhân chỉ nằm trong một split. Không có ngày nhập/xuất viện phù hợp để làm time split; kết quả không chứng minh mô hình hoạt động tốt trên bệnh viện hoặc giai đoạn khác.

So sánh baseline xác suất prevalence của train, Logistic Regression và cây quyết định giới hạn depth=3. Imputation và one-hot encoding được fit bên trong pipeline trên train. Với mỗi mô hình học được, threshold tối đa hóa F1 trên validation; test chỉ được đánh giá sau khi threshold đã cố định. Không áp dụng class weighting mặc định, vì mục tiêu là giữ xác suất và đánh giá calibration dễ diễn giải hơn.

## Kết quả trên test

| Mô hình | AP / PR-AUC | ROC-AUC | Precision | Recall | Specificity | F1 | Brier ↓ |
|---|---:|---:|---:|---:|---:|---:|---:|
{result_table}

Baseline PR-AUC gần prevalence test ({pct(float(splits['test']['positive_rate']) )}); đây là mốc để thấy AP của mô hình có vượt dự đoán không thông tin hay không. Brier score càng thấp càng tốt, nhưng cần đọc cùng đường calibration. Precision/recall thay đổi theo threshold; threshold tối ưu F1 ở validation không mặc nhiên là lựa chọn vận hành tốt nhất. Các metric là ước lượng điểm trên một lần chia dữ liệu, chưa kèm khoảng tin cậy.

![Đường Precision–Recall và ROC trên tập test](../outputs/figures/test_pr_roc_curves.png)

![Calibration trên tập test](../outputs/figures/test_calibration.png)

## Giải thích mô hình

Logistic Regression là mô hình tuyến tính, dễ kiểm tra dấu và độ lớn hệ số. Các term dưới đây có hệ số tuyệt đối lớn nhất trong lần fit này; nhóm categorical được so với nhóm tham chiếu, numeric đã scale theo train. Chỉ xuất term có support tối thiểu 200 encounter trong train; các term vẫn có thể phản ánh cách mã hóa/chuyển viện và không phải yếu tố nhân quả hay khuyến nghị lâm sàng:

{chr(10).join(term_lines)}

Cây nông dễ chuyển thành luật; mỗi lá dưới đây nêu tỷ lệ dương tính trong train, số encounter ở lá và quyết định theo threshold F1 đã chọn trên validation. Đây không phải xác suất đã được xác nhận độc lập, và cây có thể nhạy với mẫu train:

```text
{rules.strip()}
```

## Kiểm tra nhóm và giới hạn

{fairness_note} Đây là kiểm tra sơ bộ theo giới, chủng tộc, tuổi tại cùng threshold của Logistic Regression; gender/race chỉ được dùng cho audit, không làm predictor. Kiểm tra này không phải chứng nhận công bằng; nhóm nhỏ bị ẩn. Bộ dữ liệu cũ (1999–2008), chỉ gồm bệnh nhân đái tháo đường ở các cơ sở Hoa Kỳ, có missingness và thiên lệch ghi nhận. Chưa có external validation, chưa có đánh giá prospective, không thể theo dõi readmission ngoài hệ thống; chuyển viện và các biến cố cạnh tranh cần nghiên cứu riêng.

Đây là prototype giáo dục/nghiên cứu cho portfolio, không dùng cho chăm sóc lâm sàng và chưa được kiểm định/validated tại Việt Nam. Muốn cân nhắc ứng dụng ở bất kỳ cơ sở nào phải kiểm tra cohort/nhãn tại chỗ, đánh giá ngoài mẫu và theo thời gian, sai số và calibration theo nhóm, chọn threshold dựa trên nguồn lực và tác hại của cảnh báo sai/bỏ sót, rồi được hội đồng chuyên môn và quản trị dữ liệu phê duyệt.

## Tài liệu tham khảo

- Clore, J., Cios, K., DeShazo, J., & Strack, B. (2014). *Diabetes 130-US Hospitals for Years 1999–2008* [Dataset]. UCI Machine Learning Repository. [DOI 10.24432/C5230J](https://doi.org/10.24432/C5230J).
- Strack, B. et al. (2014). “Impact of HbA1c Measurement on Hospital Readmission Rates: Analysis of 70,000 Clinical Database Patient Records.” *BioMed Research International*, Article ID 781670. [DOI 10.1155/2014/781670](https://doi.org/10.1155/2014/781670).
"""
    return markdown


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("Trang ")
    fld_char1 = run._element
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instr, separate, text, end])


def make_docx(summary: dict, metrics: pd.DataFrame, terms: pd.DataFrame, rules: str, fairness: pd.DataFrame, path: Path):
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)
    styles = doc.styles
    from docx.oxml.ns import qn
    styles["Normal"].font.name = "Noto Sans"
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans")
    styles["Normal"].font.size = Pt(9.5)
    styles["Normal"].font.color.rgb = RGBColor(38, 50, 56)
    for style_name, size, color in [("Title", 22, "17324D"), ("Heading 1", 16, "17324D"), ("Heading 2", 12, "176B87")]:
        style = styles[style_name]
        style.font.name = "Noto Sans"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
    header = section.header.paragraphs[0]
    header.text = "PROJECT PORTFOLIO · HEALTH DATA SCIENCE"
    header.style = styles["Caption"]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_page_number(section.footer.paragraphs[0])

    title = doc.add_paragraph(style="Title")
    title.add_run("Dự báo nhãn early-readmission <30\ntheo UCI ở người bệnh đái tháo đường")
    subtitle = doc.add_paragraph()
    subtitle.add_run("So sánh baseline, Logistic Regression và Decision Tree nông trên dữ liệu UCI").italic = True
    subtitle.paragraph_format.space_after = Pt(12)

    cohort = summary["cohort"]
    splits = summary["splits"]
    logistic = metrics.loc[metrics["model"] == "logistic_regression"].iloc[0]
    tree = metrics.loc[metrics["model"] == "shallow_decision_tree"].iloc[0]
    baseline = metrics.loc[metrics["model"] == "prevalence_baseline"].iloc[0]
    best = "Logistic Regression" if logistic["pr_auc_average_precision"] >= tree["pr_auc_average_precision"] else "Decision Tree nông"
    best_ap = max(logistic["pr_auc_average_precision"], tree["pr_auc_average_precision"])

    doc.add_heading("Kết luận chính", level=1)
    doc.add_paragraph(
        f"Trên tập test theo nhóm bệnh nhân, {best} có Average Precision cao nhất ({best_ap:.3f}); "
        f"baseline tỷ lệ nền đạt {baseline['pr_auc_average_precision']:.3f}. Logistic Regression tại threshold chọn trên validation "
        f"đạt precision {pct(logistic['precision'])}, recall {pct(logistic['recall_sensitivity'])}; cây nông đạt "
        f"{pct(tree['precision'])} và {pct(tree['recall_sensitivity'])}. Đây là kết quả dự báo lịch sử, không phải bằng chứng "
        "rằng mô hình hoặc can thiệp theo mô hình sẽ cải thiện sức khỏe."
    )

    doc.add_heading("Dữ liệu và outcome", level=1)
    doc.add_paragraph(
        f"Nguồn: UCI Diabetes 130-US Hospitals (1999–2008), gồm {cohort['raw_rows']:,} encounter gốc tại 130 bệnh viện/hệ thống y tế Hoa Kỳ. "
        f"Bộ dữ liệu có {cohort['raw_unique_patients']:,} bệnh nhân; {cohort['raw_patients_with_multiple_encounters']:,} người xuất hiện ở nhiều encounter. "
        f"Outcome theo đúng nhãn early-readmission UCI: nhãn <30 = 1; nhãn >30 hoặc NO = 0. Cohort chính còn {cohort['eligible_encounters']:,} encounter, "
        f"trong đó {cohort['positive_encounters']:,} dương tính ({pct(cohort['positive_rate'])})."
    )
    doc.add_paragraph(
        f"Loại {cohort['excluded_death_dispositions']:,} disposition tử vong, {cohort['excluded_hospice_dispositions']:,} hospice và "
        f"cả {cohort['excluded_pending_discharge_disposition_12']:,} encounter mã 12 (still patient/expected outpatient return), vì không phải lần xuất viện hoàn tất; không có encounter mã 12 nào được giữ và không chạy sensitivity thêm lại chúng. "
        "Các ca chuyển viện vẫn được giữ; chuyển viện có thể là tiếp nối cùng đợt điều trị, nên phải diễn giải thận trọng. Nhãn <30 là early-readmission "
        "theo UCI, không khẳng định là tái nhập viện không kế hoạch và không phải thước đo CMS/risk-adjusted."
    )
    doc.add_paragraph(
        "Bài báo gốc xác định nhãn bằng lần inpatient đầu tiên tiếp theo được ghi nhận sau xuất viện; lượt từ nhà cung cấp ngoài mạng lưới không được quan sát. "
        "Dữ liệu không có ngày nhập/xuất viện phù hợp để tạo time split, vì vậy không thể kết luận về khả năng khái quát theo thời gian."
    )

    doc.add_heading("Cách xây dựng và đánh giá", level=1)
    doc.add_paragraph(
        "Đơn vị phân tích là encounter. encounter_id và patient_nbr không được dùng làm biến dự báo; patient_nbr chỉ để chia nhóm, tránh cùng một người xuất hiện "
        "ở nhiều tập. Loại weight vì thiếu rất nhiều và payer_code vì tính hành chính/khả năng khái quát thấp; gom ICD-9 thành nhóm rộng. Gender và race bị loại khỏi predictor; "
        "chỉ dùng cho subgroup audit trên dự đoán của Logistic Regression không nhận chúng làm đầu vào. Age vẫn là predictor và cũng được dùng để chia nhóm audit. Các pipeline fit imputation, "
        "scale và one-hot encoding chỉ trên train. Không dùng class_weight='balanced' mặc định để không làm thay đổi xác suất nền/cách đọc calibration."
    )
    doc.add_paragraph(
        f"Dùng StratifiedGroupKFold xấp xỉ 60/20/20: train {splits['train']['encounters']:,}, validation {splits['validation']['encounters']:,}, "
        f"test {splits['test']['encounters']:,}; không giao patient ID giữa các tập. Baseline là prevalence train; hai mô hình học là Logistic Regression "
        "và Decision Tree giới hạn depth=3. Threshold mỗi mô hình được chọn để tối đa F1 trên validation rồi cố định trước khi test."
    )
    doc.add_paragraph(
        "Metric chính là Average Precision (AP, thường được gọi là PR-AUC) vì lớp dương hiếm; so sánh với prevalence test. Bổ sung ROC-AUC, precision, recall, "
        "specificity, F1 và Brier score (càng thấp càng tốt). Đây là ước lượng điểm trên một lần chia dữ liệu, chưa có khoảng tin cậy."
    )

    doc.add_heading("Kết quả test", level=1)
    table = doc.add_table(rows=1, cols=8)
    table.style = "Light Shading Accent 1"
    headers = ["Mô hình", "AP", "ROC-AUC", "Precision", "Recall", "Specificity", "F1", "Brier"]
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
    for _, row in metrics.iterrows():
        cells = table.add_row().cells
        vals = [
            MODEL_LABELS.get(row["model"], row["model"]), dec(row["pr_auc_average_precision"]), dec(row["roc_auc"]),
            pct(row["precision"]), pct(row["recall_sensitivity"]), pct(row["specificity"]), dec(row["f1"]), dec(row["brier_score"]),
        ]
        for cell, value in zip(cells, vals):
            cell.text = str(value)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(8)
    doc.add_paragraph(
        f"Threshold test cố định: Logistic={logistic['threshold']:.3f}; cây nông={tree['threshold']:.3f}. Baseline dùng 0.5. "
        f"Prevalence test là {pct(splits['test']['positive_rate'])}; Average Precision của baseline xấp xỉ mức này. "
        "Threshold tối đa F1 chỉ là lựa chọn minh họa; vận hành thật cần dựa trên nguồn lực và chi phí của cảnh báo sai/bỏ sót."
    )
    for image, caption in [
        (OUT / "figures/test_pr_roc_curves.png", "Precision–Recall và ROC trên tập test"),
        (OUT / "figures/test_calibration.png", "Calibration trên tập test"),
    ]:
        if image.exists():
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(str(image), width=Inches(6.2))
            cap = doc.add_paragraph(caption)
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.style = styles["Caption"]

    doc.add_heading("Giải thích mô hình", level=1)
    doc.add_paragraph(
        "Hệ số Logistic Regression cho biết liên hệ có điều kiện trong mô hình, không phải quan hệ nhân quả. Với biến categorical, hệ số là so với nhóm tham chiếu; "
        "biến numeric đã scale trên train. Bảng dưới liệt kê term có hệ số tuyệt đối lớn nhất và chỉ giữ term có support ít nhất 200 encounter trong train; "
        "các hệ số vẫn có thể phản ánh biến hành chính/cách ghi nhận, "
        "nên không xem đây là yếu tố nguyên nhân hay khuyến nghị y khoa."
    )
    for _, row in terms.head(6).iterrows():
        direction = "odds cao hơn" if row["coefficient_log_odds"] > 0 else "odds thấp hơn"
        doc.add_paragraph(
            f"{row['feature']}: hệ số {row['coefficient_log_odds']:+.3f}, OR={row['odds_ratio']:.2f}, "
            f"support train={int(row['training_support']):,} ({direction}),",
            style="List Bullet",
        )
    doc.add_paragraph("Cây nông, depth tối đa 3. Mỗi lá nêu tỷ lệ dương tính trong train, support của lá và quyết định theo threshold chọn từ validation; đây không phải xác suất đã được xác nhận độc lập:")
    rules_p = doc.add_paragraph()
    rules_run = rules_p.add_run(rules.strip())
    rules_run.font.name = "Consolas"
    rules_run.font.size = Pt(7.5)

    doc.add_heading("Giới hạn và sử dụng có trách nhiệm", level=1)
    doc.add_paragraph(
        "Dữ liệu lịch sử Hoa Kỳ 1999–2008 chỉ đại diện bệnh nhân đái tháo đường tại các cơ sở tham gia; có missingness, giới hạn ghi nhận ngoài mạng lưới và không có external validation. "
        "Không thể time-split. Phân tích subgroup theo giới, chủng tộc, tuổi dùng cùng threshold logistic, chỉ xuất nhóm có tối thiểu 200 encounter; gender/race chỉ dùng cho audit, không làm predictor. Đây là kiểm tra sơ bộ, không chứng nhận công bằng. "
        "Đây là prototype giáo dục/nghiên cứu, không dùng cho chăm sóc lâm sàng và chưa được kiểm định/validated tại Việt Nam. Trước khi cân nhắc ứng dụng phải kiểm định ngoài mẫu/cơ sở, đánh giá prospective, calibration, sai số theo nhóm và quy trình phê duyệt chuyên môn."
    )
    doc.add_heading("Nguồn", level=1)
    doc.add_paragraph("Clore, J., Cios, K., DeShazo, J., & Strack, B. (2014). Diabetes 130-US Hospitals for Years 1999–2008 [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5230J")
    doc.add_paragraph("Strack, B. et al. (2014). Impact of HbA1c Measurement on Hospital Readmission Rates: Analysis of 70,000 Clinical Database Patient Records. BioMed Research International, Article ID 781670. https://doi.org/10.1155/2014/781670")
    doc.add_paragraph("Các kết quả là từ lần chạy thật của scripts/run_analysis.py trên CSV chính thức UCI; không có dữ liệu dòng-level hoặc patient ID trong báo cáo.")
    doc.save(path)


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    summary_path = OUT / "tables/analysis_summary.json"
    if not summary_path.exists():
        raise SystemExit("Run scripts/run_analysis.py first to create real-data metrics.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = pd.read_csv(OUT / "tables/model_metrics.csv")
    terms = pd.read_csv(OUT / "tables/logistic_top_terms.csv")
    fairness_path = OUT / "tables/subgroup_metrics.csv"
    fairness = pd.read_csv(fairness_path) if fairness_path.exists() else pd.DataFrame()
    rules_path = OUT / "tables/shallow_tree_rules.txt"
    rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""

    md_path = REPORTS / "portfolio_report.md"
    md_path.write_text(make_report_markdown(summary, metrics, terms, rules, fairness), encoding="utf-8")
    docx_path = REPORTS / "portfolio_report.docx"
    make_docx(summary, metrics, terms, rules, fairness, docx_path)
    pdf_path = REPORTS / "portfolio_report.pdf"
    libreoffice = subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", str(REPORTS), str(docx_path)],
        capture_output=True, text=True,
    )
    if libreoffice.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(f"LibreOffice PDF export failed: {libreoffice.stderr or libreoffice.stdout}")
    print(f"Created {md_path}, {docx_path}, and {pdf_path}")

if __name__ == "__main__":
    main()
