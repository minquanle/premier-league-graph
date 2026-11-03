# Chiến lược thu bài (v2) – ưu tiên lượng thông tin, giảm lệch PLAYS_FOR

Cập nhật 30/10/2026, dựa trên số liệu pilot 300 bài (teacher `ag/gemini-3-flash`) và khảo sát nguồn cùng ngày.

## 1. Pilot cho thấy gì

- 156/300 bài pilot đăng tháng 10/2026, **ngoài kỳ chuyển nhượng**: chuyên mục chuyển nhượng lúc đó gần như chỉ có tin đồn
  ("nhắm", "tranh mua"), bị loại khỏi mạng (status `rumor`).
- Bài giàu quan hệ hiếm nhất là **bài tổng kết kỳ chuyển nhượng / ngày chốt sổ** ("United offload Lukaku, Spurs strengthen
  as window shuts", 08/2019: 22 quan hệ hiếm), bài **biến động HLV/ban lãnh đạo** và bài **tường thuật trận** (nhiều PLAYS_FOR,
  thỉnh thoảng có chuyển nhượng/cho mượn được nhắc lại).
- Bài 0 quan hệ: livescore, lịch thi đấu, nhận định/soi kèo, trang lỗi/tiêu đề là tên báo, tin vắn nhiều môn.
- Bài dài cho nhiều quan hệ hơn (trên 6.000 ký tự: 12,3 quan hệ/bài, không bài nào 0) → **không lọc theo độ dài**.
- Phân loại tiêu đề bằng từ khoá "chuyển nhượng" không dự báo tốt: tiêu đề tin đồn và tin đã chốt dùng cùng từ.
  Cần tách từ **đã xảy ra** (chính thức, hoàn tất, ra mắt, gia nhập, sa thải, bổ nhiệm, signs, completes, sacked, appointed)
  khỏi từ **tin đồn** (nhắm, muốn, tranh mua, theo đuổi, linked, target, eye, set to).

## 2. Khảo sát khả năng lấy bài theo thời gian

| Nguồn | Cách lấy bài cũ | Độ sâu | Ghi chú |
|---|---|---|---|
| VnExpress (vi) | Trang chủ đề `/chu-de/<ten>-<id>-pN` (CLB, cầu thủ, HLV) | ~20 trang ≈ 01/2026 → nay | Phủ trọn kỳ chuyển nhượng 01/2026 và hè 2026. Chuyên mục `-pN` và sitemap ngày không dùng được (trả trang chủ / không lọc ngày) |
| Bóng đá Plus, 24h (vi) | Chuyên mục chỉ trả bài mới (tham số trang bị bỏ qua); sitemap 24h chỉ bài hiện tại | Hiện tại | Dùng cho bài mới + đi theo "bài liên quan" có lọc ngày |
| Nhân Dân EN, Việt Nam News (en) | Sitemap tháng `/sitemaps/news-YYYY-M.xml` | 2017 → nay | ~15–30 bài liên quan Ngoại hạng Anh/tháng mỗi báo; slug URL chứa tiêu đề nên lọc được trước khi tải |
| VnExpress International (en) | API `listcategory` | Nông | Bổ sung nhỏ |
| Wayback Machine | CDX | — | Đang tạm ngừng khi thử; không dựa vào |

## 3. Chiến lược

1. **Nhắm theo thời điểm** thay vì lấy "mới nhất":
   - Kỳ chuyển nhượng hè (15/6–5/9) và đông (1/1–5/2) mọi năm có lưu trữ; ưu tiên tuần chốt sổ.
   - Giai đoạn biến động HLV (thường 10–12 và 3–5).
   - Tiếng Việt: hè 2026 và 01/2026 qua trang chủ đề VnExpress. Tiếng Anh: 2017–2026 qua sitemap tháng.
2. **Khám phá theo thực thể**: trang chủ đề VnExpress của 20 CLB đang đá + các CLB từng đá Ngoại hạng Anh + HLV/cầu thủ lớn;
   mở rộng dần bằng tag chủ đề tìm thấy trong bài đã lấy (snowball), có giới hạn số trang/chủ đề.
3. **Lọc trước khi tải / trước khi gọi LLM** (rẻ, dựa trên tiêu đề + slug + mô tả):
   - Loại: livescore, lịch thi đấu, nhận định, soi kèo, dự đoán, video/highlight/ảnh, "tin mới nhất bóng đá tối", tin vắn nhiều
     môn ("in brief", "sports news"), bóng đá Việt Nam/nữ/U-age (trừ khi có CLB Ngoại hạng Anh), trang không có tiêu đề thật.
   - Chấm điểm ưu tiên: từ sự kiện đã xảy ra (+), tên CLB/HLV Ngoại hạng Anh (+), từ tin đồn (−).
4. **Hạn mức theo loại bài** cho mỗi lô (để giảm lệch PLAYS_FOR mà vẫn đủ nút):
   - ~55% sự kiện (chuyển nhượng/cho mượn/gia hạn/HLV/ban lãnh đạo đã chốt),
   - ~25% phân tích/phỏng vấn/xung đột (CRITICIZES, CONFLICTS_WITH, REPRESENTS),
   - ~20% tường thuật trận (PLAYS_FOR, MANAGES – nguồn nút chính),
   - tin đồn thuần ≤ 5% (đủ để student học nhãn `rumor`).
5. **Vòng phản hồi**: thu theo lô ~200 bài → teacher → đo quan hệ đủ điều kiện/bài theo nguồn, chủ đề, năm, từ khoá →
   tăng trọng số nhóm cho nhiều quan hệ hiếm, giảm nhóm kém. Dừng một nguồn/chủ đề khi số nút mới/bài giảm mạnh.
6. **Không lấy quá nhiều bài cùng một sự kiện**: tối đa 3 bài/sự kiện (cùng cặp thực thể + loại + tuần) – đủ làm bằng chứng nhiều nguồn,
   không tốn token cho bản lặp.

## 4. Mục tiêu định lượng cho lô mở rộng

- ~2.000 bài sạch (≈1.100 vi, 900 en), ước tính ≥ 2.000 nút và ≥ 5.000 cạnh sau gom tên/lọc phạm vi.
- PLAYS_FOR ≤ 55% số cạnh; mỗi loại quan hệ có ≥ 30 cạnh (REPRESENTS/CONFLICTS_WITH: thu có chủ đích, chấp nhận ít hơn nếu báo không có).
- Theo dõi riêng tỷ lệ tin đồn/phủ định để báo cáo, không đưa vào mạng.
