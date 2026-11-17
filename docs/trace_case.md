# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

## Case: `seeded_regression` C-002@11

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, report `E-seeded_regression-20261117-7511` (`src/reports/eval_seeded_regression.json`) |
| Triệu chứng | `pass_rate` FAIL (status `resolved` ≠ `denied`), `refund_amount_correct` FAIL, `db_fact_kept` FAIL. Ledger ghi **20.000đ / 1 dòng**, label 0đ / 0 dòng. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." (khớp ledger, nên `reply_matches_ledger` không bắt; lỗi nằm ở tiền chứ không ở lời) |
| Bước sai đầu tiên | **Event 3 `model_override`** — `{"delay_days": 5}`: số ngày trễ khách khai ("Đơn giao trễ 5 ngày") đè lên `delay_days` của DB |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng hợp đồng (allowed_tools chỉ đọc). Event 2 `investigator` đọc đúng đơn O-202 từ DB, visible=ok. Mọi sai lệch sau đó (event 4 `quote` 20.000đ theo SHIP-LATE-03, ghi ledger, reply) đều suy ra từ giá trị bị đè ở event 3 |
| Failure mode | **FM-03 ticket_trust** (`docs/failure_taxonomy.md`) |
| Đối chứng | `fixed C-002@11`: không có event `model_override`; event 3 `quote {"amount_vnd": 0, "reason": "no_entitlement"}` → reply từ chối, ledger 0 → PASS |
| Regression test | `src/tests/test_lab9_trace_case.py::test_c002_ticket_trust_is_caught_on_seeded_regression` (assert `db_fact_kept`/`refund_amount_correct`/`pass_rate` False, 2 bước đầu đúng) và `::test_c002_fixed_keeps_db_fact_and_denies`. Đã chạy: fail-detect trên `seeded_regression`, pass trên `fixed` (14 passed) |
| Chưa chắc chắn | Trace không cho biết *vì sao* model chọn tin ticket (MockLLM là luật cố định `seed % 5` / công tắc `ticket_trust`); với LLM thật cần xem prompt + log lấy mẫu. Trace cũng không chứng minh lỗi chỉ xảy ra khi ticket nêu số ngày — claim không nêu số ngày thì không kiểm được |

Kèm thêm `test_c008_misreport_is_caught_by_fm08_metric`: C-008@11 qua grader Lab 8 nhưng reply nói 35.000đ trong khi ledger 20.000đ, chỉ metric FM-08 `reply_matches_ledger` bắt được.

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
