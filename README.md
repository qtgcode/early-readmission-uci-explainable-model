# Dự báo nhãn early-readmission `<30` theo UCI ở người bệnh đái tháo đường

Project portfolio về dự báo encounter được gắn nhãn tái nhập viện sớm trong bộ dữ liệu **Diabetes 130-US Hospitals (1999–2008)**. Trọng tâm là pipeline có thể chạy lại, tránh rò rỉ bệnh nhân giữa các tập và so sánh mô hình có thể giải thích: baseline tỷ lệ nền, Logistic Regression và Decision Tree nông.

> Đây là prototype giáo dục/nghiên cứu cho portfolio trên dữ liệu lịch sử của Hoa Kỳ; không phải công cụ lâm sàng, không được dùng để quyết định chăm sóc bệnh nhân và chưa được kiểm định/validated tại Việt Nam.

## Kết quả và thành phần

Sau khi chạy pipeline, các chỉ số tổng hợp nằm trong `outputs/tables/model_metrics.csv` và `outputs/tables/analysis_summary.json`; biểu đồ PR/ROC và calibration nằm trong `outputs/figures/`. Báo cáo tiếng Việt được tạo ở `reports/`. Không có dự đoán từng dòng, ID bệnh nhân, ID encounter hay CSV gốc được xuất vào các thư mục này.

- `src/readmission/pipeline.py`: quy tắc cohort, nhóm ICD-9, chia nhóm, tiền xử lý và metric.
- `scripts/download_data.py`: tải bản UCI chính thức vào vùng dữ liệu thô cục bộ.
- `scripts/run_analysis.py`: chạy EDA tối thiểu, train/validation/test và xuất các tổng hợp an toàn.
- `scripts/build_report.py`: tạo báo cáo DOCX/Markdown từ kết quả thực nghiệm.
- `sql/01_profile_and_define_cohort.sql`: phần bổ trợ SQL Server/T-SQL để nạp, kiểm tra và định nghĩa outcome/cohort.
- `tests/`: kiểm thử logic cohort, tránh leakage và chọn ngưỡng.

## Tái lập trên máy cá nhân

Dùng Python 3.10 trở lên. Tại thư mục project:

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/download_data.py
python scripts/run_analysis.py
python scripts/build_report.py
python -m unittest discover -s tests -v
```

Tải dữ liệu cần kết nối Internet. Tập `diabetic_data.csv` và `IDS_mapping.csv` được giữ trong `data/raw/`, thư mục này bị Git ignore. `scripts/build_report.py` tạo DOCX/Markdown và cần LibreOffice cài sẵn để xuất PDF. Các kết quả không có dữ liệu từng người có thể commit sau khi được rà soát.

## Dữ liệu, cohort và outcome

Nguồn là UCI Machine Learning Repository, dataset 296: [Diabetes 130-US Hospitals for Years 1999–2008](https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008), DOI [10.24432/C5230J](https://doi.org/10.24432/C5230J). UCI mô tả 101.766 encounter tại 130 bệnh viện/hệ thống y tế Hoa Kỳ; dữ liệu có thông tin nhạy cảm như tuổi, giới và chủng tộc.

Một hàng là một encounter. Outcome nhị phân theo đúng nhãn UCI: `readmitted == '<30'` thành 1; `'>30'` hoặc `'NO'` thành 0. Đây là nhãn early-readmission của bộ dữ liệu, **không khẳng định đó là tái nhập viện không kế hoạch**, không phải thước đo risk-adjusted/CMS và không đồng nghĩa với mọi định nghĩa vận hành của chỉ số “30-day readmission”.

Quy tắc cohort được công khai trong mã:

- Loại các disposition được UCI ánh xạ là tử vong (11, 19, 20, 21) và hospice (13, 14) vì đây là biến cố cạnh tranh/nhóm đích không phù hợp với endpoint tái nhập viện thông thường. Cũng loại **cả 3 encounter** disposition 12 (“still patient or expected to return for outpatient services”): đây không thể hiện lần xuất viện hoàn tất; không encounter mã 12 nào nằm trong cohort chính và không chạy sensitivity riêng bằng cách thêm lại chúng.
- Giữ các disposition chuyển viện/chuyển cơ sở; mã destination được xem là biến phân loại có sẵn khi ra viện. Tuy nhiên, chuyển viện có thể là tiếp nối cùng đợt chăm sóc chứ không phải tái nhập viện thực sự, nên kết quả cho nhóm này phải diễn giải thận trọng.
- Gom chẩn đoán ICD-9 thành nhóm rộng để giảm cardinality và tăng khả năng đọc.
- Không dùng `encounter_id` hoặc `patient_nbr` làm predictor. `patient_nbr` chỉ được dùng để đảm bảo các encounter của cùng người không xuất hiện ở nhiều tập.
- Loại `weight` do thiếu rất nhiều giá trị và `payer_code` do tính hành chính/khả năng khái quát thấp. Không dùng outcome để xây đặc trưng.
- Loại `gender` và `race` khỏi đặc trưng của các mô hình chính; chỉ dùng các thuộc tính này trong audit subgroup trên dự đoán của mô hình không nhận chúng làm đầu vào. `age` vẫn là predictor và cũng được dùng để chia nhóm audit. Audit sơ bộ không phải chứng nhận công bằng.
- Các trường `?` được xem là thiếu; imputation và one-hot encoding được fit trong pipeline trên tập train.

Bài báo gốc mô tả nhãn được xác định bằng lần inpatient đầu tiên tiếp theo được ghi nhận sau khi xuất viện; các lượt khám ngoài mạng lưới không được quan sát. Vì dataset không cung cấp ngày nhập viện/xuất viện phù hợp để dựng mốc thời gian, project dùng group split theo bệnh nhân thay cho time split. Kết quả vì vậy không đo được khả năng khái quát theo thời gian hoặc sang hệ thống y tế khác.

## Thiết kế đánh giá

Chia dữ liệu xấp xỉ 60/20/20 bằng `StratifiedGroupKFold`, bảo đảm tập train, validation và test không có patient ID giao nhau. Random seed là 42. Tập validation chỉ dùng chọn threshold; test chỉ dùng báo cáo kết quả cuối. Không có time split vì dữ liệu phát hành không cung cấp mốc thời gian encounter để làm việc đó.

So sánh ba mức:

1. **Baseline tỷ lệ nền**: xác suất hằng bằng prevalence của train; không học đặc trưng.
2. **Logistic Regression**: mô hình tuyến tính; mã hóa one-hot cho biến phân loại, impute/scale biến số. Hệ số biểu diễn liên hệ có điều kiện, không phải quan hệ nhân quả.
3. **Decision Tree nông**: depth tối đa 3, tối thiểu 2% mẫu train tại mỗi lá; cung cấp luật dễ đọc nhưng có thể kém ổn định.

Metric chính là **Average Precision (PR-AUC)** vì outcome dương tính ít hơn nhiều so với âm tính. Báo cáo thêm ROC-AUC, precision, recall/sensitivity, specificity, F1 và Brier score. Với mỗi mô hình học được, threshold được chọn trên validation để tối đa F1, rồi giữ nguyên trên test. Đây là lựa chọn minh họa; triển khai thật cần chọn threshold theo năng lực can thiệp, chi phí bỏ sót/cảnh báo nhầm và đánh giá lâm sàng. Brier score càng thấp càng tốt; PR-AUC phải so với prevalence trên test.

Có kiểm tra tổng hợp theo giới, chủng tộc và nhóm tuổi cho Logistic Regression, chỉ xuất nhóm có ít nhất 200 encounter. Giới và chủng tộc chỉ dùng cho subgroup audit, không phải predictor của mô hình. Đây là chẩn đoán sơ bộ, không phải chứng nhận công bằng. Bảng hệ số logistic chỉ giữ term có ít nhất 200 encounter trong train; các hệ số vẫn có thể phản ánh cách mã hóa/biến hành chính, không phải yếu tố nhân quả hay khuyến nghị lâm sàng.

## SQL Server/T-SQL

Script SQL là phần bổ trợ để trình bày kỹ năng SQL: nạp CSV vào staging NVARCHAR, kiểm tra target/mã disposition, tạo view cohort và xem tỷ lệ outcome ở dạng tổng hợp. Sửa đường dẫn file theo máy SQL Server. Dữ liệu staging vẫn phải được giữ cục bộ, không đưa lên GitHub.

## Bảo mật và giấy phép

**Không commit dữ liệu gốc, dữ liệu đã làm sạch ở mức dòng, patient/encounter ID, dự đoán từng encounter, hay file model/pickle.** `.gitignore` chặn các vị trí dữ liệu thô, prediction và model; hãy kiểm tra `git status` trước mọi lần push. Chỉ các bảng tổng hợp và hình vẽ đã rà soát mới được chia sẻ.

Dữ liệu UCI được cấp phép [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); ghi công và citation được lưu riêng trong [`DATA_LICENSE.md`](DATA_LICENSE.md). **Code có file [`LICENSE`](LICENSE) riêng nhưng license sử dụng chưa được chọn**; hiện chưa cấp quyền tái sử dụng code. Không được hiểu CC BY 4.0 của dataset là license của code. Chủ repo cần quyết định code license riêng trước khi cho phép tái sử dụng.

## Tài liệu tham khảo

- Clore, J., Cios, K., DeShazo, J., & Strack, B. (2014). *Diabetes 130-US Hospitals for Years 1999–2008* [Dataset]. UCI Machine Learning Repository. [https://doi.org/10.24432/C5230J](https://doi.org/10.24432/C5230J).
- Strack, B. et al. (2014). “Impact of HbA1c Measurement on Hospital Readmission Rates: Analysis of 70,000 Clinical Database Patient Records.” *BioMed Research International*, Article ID 781670. [https://doi.org/10.1155/2014/781670](https://doi.org/10.1155/2014/781670).

## Kết quả thực nghiệm

Các số dưới đây lấy từ lần chạy pipeline trên CSV UCI sau khi loại `gender` và `race` khỏi predictor. Cohort có **99.340 encounter**, prevalence nhãn early-readmission UCI `<30` là **11,39%**; chia theo bệnh nhân thành train 59.602, validation 19.870, test 19.868; test có 2.262 nhãn dương tính. Outcome không phải thước đo CMS/risk-adjusted và không khẳng định readmission không kế hoạch. Không dùng số liệu mô phỏng.

| Mô hình | AP / PR-AUC | ROC-AUC | Precision | Recall | Specificity | F1 | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline prevalence | 0,114 | 0,500 | 0,0% | 0,0% | 100,0% | 0,000 | 0,101 |
| Logistic Regression | **0,230** | **0,666** | 19,7% | 48,4% | 74,7% | **0,280** | **0,096** |
| Decision Tree nông | 0,190 | 0,645 | 17,4% | **55,1%** | 66,3% | 0,264 | 0,097 |

Threshold của Logistic Regression là 0,124 và của cây nông là 0,140; cả hai được chọn để tối đa F1 trên validation, không phải test. Baseline dùng threshold 0,5 nên không dự đoán ca dương tính; AP của nó là mốc so sánh với prevalence test. Xem [báo cáo DOCX](reports/portfolio_report.docx), [báo cáo PDF](reports/portfolio_report.pdf), bảng [metrics](outputs/tables/model_metrics.csv) và [biểu đồ](outputs/figures/test_pr_roc_curves.png).
