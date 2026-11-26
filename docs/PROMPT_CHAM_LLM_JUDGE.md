# Prompt chấm chất lượng graph (LLM-as-judge)

Dán toàn bộ khối dưới đây cho một AI agent có thể chạy code và truy cập web (ví dụ Claude Code, ChatGPT agent, Codex). Không dùng Gemini, vì Gemini 3 Flash là mô hình teacher của dự án.

---

````text
Bạn là người chấm độc lập (LLM-as-judge) cho một đồ thị tri thức về bóng đá Ngoại hạng Anh. Nhiệm vụ: lấy một mẫu ngẫu nhiên 400 cạnh từ repository công khai, đọc bài báo nguồn của từng cạnh, rồi chấm cạnh đó đúng hay sai.

## 1. Lấy dữ liệu

Repository: https://github.com/minquanle/premier-league-graph (chỉ đọc, KHÔNG commit, KHÔNG mở issue/PR).

Hai file cần dùng:
- dataset/graph/edges.csv: relation_id, subject_id, subject, relation, object_id, object, evidence_count, languages, first_seen, last_seen
- dataset/graph/edge_sources.csv: relation_id, article_id, url (mỗi cạnh có một hoặc nhiều bài báo làm bằng chứng)

Lấy mẫu ĐÚNG bằng đoạn code sau (để kết quả lặp lại được), không tự chọn cạnh:

```python
import csv, random
edges = list(csv.DictReader(open("dataset/graph/edges.csv", encoding="utf-8")))
sample = random.Random(2026).sample(edges, 400)
```

Kiểm tra: edges có 4924 dòng; mẫu có 217 cạnh PLAYS_FOR và 61 cạnh TRANSFERRED_TO. Nếu không khớp, dừng lại và báo.

## 2. Ý nghĩa 12 loại quan hệ (chủ thể → đối tượng)

- PLAYS_FOR: cầu thủ → CLB, cầu thủ đang/đã thi đấu cho CLB
- TRANSFERRED_TO: cầu thủ → CLB nhận, chuyển nhượng (đã hoàn tất hoặc được công bố)
- LOANED_TO: cầu thủ → CLB nhận, cho mượn
- EXTENDED_CONTRACT_WITH: người → CLB, gia hạn hợp đồng
- NEGOTIATES_TO_SIGN: CLB → người, CLB đàm phán/muốn chiêu mộ người đó (tin đồn đàm phán vẫn tính là đúng)
- MANAGES: HLV → CLB, đang/đã dẫn dắt
- APPOINTED_MANAGER_OF: HLV → CLB, được bổ nhiệm làm HLV
- DISMISSED_MANAGER: CLB → HLV, CLB sa thải HLV
- RESIGNED_FROM: HLV → CLB, HLV từ chức
- REPRESENTS: người đại diện → cầu thủ/HLV
- CRITICIZES: người → người/CLB, chỉ trích công khai
- CONFLICTS_WITH: người → người/CLB, mâu thuẫn, xung đột

## 3. Cách chấm mỗi cạnh

1. Mở bài báo theo url trong edge_sources.csv. Một cạnh có nhiều bài thì đọc lần lượt, TỐI ĐA 3 bài; chỉ cần một bài ủng hộ là đủ.
2. Chấm theo NỘI DUNG BÀI BÁO, không theo hiểu biết bóng đá của bạn. Bài viết tiếng Việt hoặc tiếng Anh. Tên có thể viết khác (MU = Manchester United, Man City = Manchester City, tên không dấu...).
3. Gán nhãn:
   - D (đúng): đúng người, đúng CLB, đúng loại quan hệ, đúng chiều, và bài báo nêu rõ hoặc ngụ ý rõ ràng quan hệ đó tại thời điểm đăng (ví dụ "Cleverley chấn thương, MU mất bốn tiền vệ" ⇒ Cleverley PLAYS_FOR Manchester United là D).
   - S (sai): bài báo có đọc được nhưng không ủng hộ cạnh.
   - K (không đủ căn cứ): không mở được bài nào (404, paywall, chặn truy cập) hoặc bài quá mơ hồ để kết luận.
4. Nếu S, ghi loai_loi bằng MỘT mã:
   - sai_nguoi: nhầm người (cùng họ, sai thực thể)
   - sai_clb: nhầm CLB (thường là nhầm sang đội đối thủ trong trận)
   - sai_chieu: đúng hai thực thể nhưng ngược chiều
   - sai_loai: có quan hệ nhưng sai loại (ví dụ đàm phán bị ghi thành đã chuyển nhượng; HLV bị ghi PLAYS_FOR)
   - khong_phai_su_that: bài nói đây là tin đồn đã bị bác bỏ, giả định, hoặc sự kiện chưa xảy ra
   - khac: lý do khác (giải thích trong ghi_chu)
5. Không bỏ qua cạnh nào. Không tự sửa dữ liệu.

## 4. Đầu ra (bắt buộc đúng định dạng)

File 1: cham_llm_judge_400.csv, mã hoá UTF-8, đúng 400 dòng theo đúng thứ tự trong `sample`, các cột:

stt,relation_id,subject,relation,object,nhan,loai_loi,url_da_doc,ghi_chu

- stt: 1..400
- nhan: D, S hoặc K
- loai_loi: để trống nếu D hoặc K
- url_da_doc: url bài báo đã dùng để kết luận (nhiều url cách nhau bằng |)
- ghi_chu: tối đa 25 từ, lý do ngắn gọn bằng tiếng Việt. KHÔNG chép nguyên đoạn dài từ bài báo.

File 2: cham_llm_judge_400_meta.json:

{
  "judge_model": "<tên và phiên bản chính xác của mô hình bạn>",
  "date": "<YYYY-MM-DD>",
  "seed": 2026,
  "n": 400,
  "counts": {"D": 0, "S": 0, "K": 0},
  "error_types": {"<mã lỗi>": 0},
  "articles_opened": 0,
  "articles_failed": 0,
  "notes": "<khó khăn, quy tắc phải tự quyết khi gặp trường hợp mơ hồ>"
}

Cuối cùng, in ra: số D/S/K và precision = D / (D + S) (không tính K).
````

---

## Sau khi nhận kết quả

Gửi lại hai file `cham_llm_judge_400.csv` và `cham_llm_judge_400_meta.json`, đặt vào `reports/`. Từ đó sẽ tính precision kèm khoảng tin cậy Wilson, phân bố lỗi theo loại quan hệ, rồi cập nhật bài báo, báo cáo và `dataset/evaluation/`.
