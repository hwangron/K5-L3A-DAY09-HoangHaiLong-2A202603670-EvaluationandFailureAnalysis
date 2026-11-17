# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (judge replay, FAIL là lớp dương).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** (tp 24, fp 4, fn 6, tn 66) | **dùng** (không chặn release một mình; chỉ cho tiêu chí giọng văn) |
| helpful | 100 | 0.375 | 1.000 | **0.300** | **Không đạt** κ và R (tp 9, fp 0, fn 21, tn 70) | **loại** |

Tỉ lệ fail quan sát → hiệu chỉnh Rogan-Gladen: tone 0.28 → 0.30; helpful 0.09 → 0.30 (judge helpful báo thấp hơn thực tế hơn 3 lần).

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã với khách khiếu nại. Rubric: liệt kê đại từ được phép (anh/chị, quý khách) và cấm "nha/nhé" trong khiếu nại |
| 2 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Judge thấy "Dạ, vui lòng" là lịch sự, bỏ qua giọng ra lệnh. Rubric: chấm cả câu, câu ra lệnh/cắt ngang khách = FAIL dù có kính ngữ |
| 3 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu xin lỗi. Rubric: xin lỗi **không bắt buộc** khi kết quả có lợi cho khách; ngắn mà đủ ý = PASS |
| 4 | H-051 "…đã refund 90.000đ…" | FAIL | PASS | Người | CS LogiX cho phép từ "refund". Rubric: thêm danh sách thuật ngữ tiếng Anh được phép |
| 5 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm…" | PASS | FAIL | Người | Hứa ngoài kết quả hệ thống — thực chất là lỗi nội dung (FM-08 họ hàng), không phải giọng văn. Rubric tone không bắt được; chuyển sang grader code đối chiếu ledger |
| 6 | H-070 "…ạ 😂😂" | PASS | FAIL | Người | Rubric: emoji cười trong ngữ cảnh khiếu nại = FAIL |
| 7 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Nhãn người sai (đọc nhầm ticket). Hành động: **sửa nhãn người** trong tập calibration, không sửa rubric |
| 8 | H-094 "Da, LogiX da hoan…" (mất dấu) | PASS | FAIL | Người | Rubric: tiếng Việt không dấu/dịch máy = FAIL |
| 9 | HL-015 "…đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người | Judge helpful không thấy ledger nên không thể biết số tiền sai. Đây là FM-08 → chấm bằng code `reply_matches_ledger`, không bằng judge |
| 10 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Không nói kết quả/số tiền; judge chấm theo giọng tự tin. Rubric helpful phải yêu cầu nêu kết quả + số tiền + thời hạn |

## Kết luận
- **helpful bị loại:** recall 0.30 — judge bỏ lọt 21/30 câu trả lời không hữu ích (fn = 21), κ = 0.375 < 0.6. Phần lớn fn là câu trả lời dài, xin lỗi nhiều lần nhưng **nói sai số tiền so với ledger** (HL-015, HL-016, HL-020…): judge bị độ "lịch sự" đánh lừa và không có quyền xem ledger. Precision 1.0 không cứu được: một gate dựa trên judge này sẽ cho qua 70% lỗi. Lỗi số tiền là tiền ⇒ chấm bằng code (`reply_matches_ledger`, `refund_amount_correct`), không bao giờ bằng judge.
- **tone_ok dùng được** cho tiêu chí giọng văn (đạt cả ba floor), nhưng vẫn bỏ lọt 6/30 (fn): suồng sã, ra lệnh, emoji, mất dấu. Sửa rubric theo các dòng trên rồi calibrate lại.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật. Ít nhất 1 nhãn người sai (H-081) ⇒ cần 2 người gán nhãn độc lập cho tập calibration.
